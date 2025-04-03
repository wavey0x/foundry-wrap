"""
Safe Transaction Management for fwrap

This module provides functionality to create and sign Gnosis Safe transactions
from Foundry forge script executions.
"""

import subprocess
import json
import os
from typing import Optional, Dict, Any, NamedTuple, Tuple, List
from pathlib import Path
import sys
import asyncio
from asyncio.subprocess import Process
import requests
import argparse
from eth_hash.auto import keccak
from gnosis.safe import Safe
from gnosis.eth import EthereumClient
from gnosis.safe.safe import SafeV111, SafeV120, SafeV130, SafeV141
from gnosis.safe.enums import SafeOperationEnum
from gnosis.safe.multi_send import MultiSend, MultiSendOperation, MultiSendTx
from gnosis.safe.safe_tx import SafeTx
from gnosis.safe.signatures import signature_split, signature_to_bytes
from gnosis.safe.api import TransactionServiceApi
from gnosis.safe.safe_signature import SafeSignature
from .config import GLOBAL_CONFIG_PATH
from rich.console import Console

# Create a console instance at the module level
console = Console()

# Default values
NULL_ADDRESS = "0x0000000000000000000000000000000000000000"

# SafeTransaction object to store all the data needed for a transaction
class SafeTransaction(NamedTuple):
    safe_address: str
    to: str
    value: int
    data: bytes
    operation: int
    safe_tx_gas: int
    base_gas: int = 0
    gas_price: int = 0
    gas_token: str = NULL_ADDRESS
    refund_receiver: str = NULL_ADDRESS
    safe_nonce: int = 0
    safe_tx_hash: bytes = b""

class ForgeScriptRunner:
    """Runs Foundry forge scripts and captures their output"""
    
    def __init__(self, rpc_url: str, project_root: str = None):
        self.rpc_url = rpc_url
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd()
        
    async def _stream_output(self, stream, is_stderr=False):
        """Stream process output in real-time"""
        while True:
            line = await stream.readline()
            if not line:
                break
            line = line.decode().rstrip()
            if is_stderr:
                print(line, file=sys.stderr, flush=True)
            else:
                print(line, flush=True)

    async def _run_forge_script_async(self, script_path: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Run forge script asynchronously and capture output"""
        command = [
            "forge", "script",
            script_path,
            "--rpc-url", self.rpc_url,
            "-vvv"
        ]
        
        try:
            process: Process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root
            )

            # Create tasks for streaming stdout and stderr
            stdout_task = asyncio.create_task(self._stream_output(process.stdout))
            stderr_task = asyncio.create_task(self._stream_output(process.stderr, is_stderr=True))
            
            # Wait for the process to complete and output to be streamed
            await asyncio.gather(stdout_task, stderr_task)
            return_code = await process.wait()

            if return_code != 0:
                return False, None, f"Forge script failed with return code {return_code}"

            # Find and parse the latest run JSON file
            broadcast_dir = self.project_root / "broadcast"
            script_name = Path(script_path).name
            latest_run = self._find_latest_run_json(broadcast_dir / script_name / "1" / "dry-run")
            
            if latest_run:
                with open(latest_run) as f:
                    return True, json.load(f), None
            
            return False, None, "Could not find last run data. Make sure script is in a broadcast block."
            
        except Exception as e:
            return False, None, f"Error running forge script: {str(e)}"

    def run_forge_script(self, script_path: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Runs forge script and returns (success, json_data, error_message)"""
        return asyncio.run(self._run_forge_script_async(script_path))

    def _find_latest_run_json(self, directory: Path) -> Optional[Path]:
        """Find the latest run-*.json file in the directory"""
        if not directory.exists():
            return None
        
        json_files = list(directory.glob("run-*.json"))
        if not json_files:
            return None
        
        return max(json_files, key=lambda x: x.stat().st_mtime)

class SafeTransactionBuilder:
    """Builds Gnosis Safe transactions from forge output"""
    
    def __init__(self, safe_address: str, rpc_url: str):
        self.safe_address = checksum_address(safe_address)
        # Gnosis Safe MultiSend contract address (same across all networks)
        self.multisend_address = "0x40A2aCCbd92BCA938b02010E17A5b8929b49130D"
        self.rpc_url = rpc_url
        self.ethereum_client = EthereumClient(self.rpc_url)
        self.safe = Safe(self.safe_address, self.ethereum_client)
        self.multisend = MultiSend(self.ethereum_client, self.multisend_address, call_only=True)
        
    def build_safe_tx(self, forge_output: Dict[str, Any], nonce: int = None) -> SafeTx:
        """
        Builds Safe transaction from forge output
        Batches all transactions through the MultiSend contract
        """
        # Extract transactions from forge output
        txs = []
        for tx in forge_output["transactions"]:
            # Skip transactions to the console logger
            if tx['transaction']['to'].lower() == '0x000000000000000000636f6e736f6c652e6c6f67':
                continue
                
            txs.append(MultiSendTx(
                MultiSendOperation.CALL, 
                tx['transaction']['to'], 
                int(tx['transaction']['value'], 16),  # Convert hex string to int
                tx['transaction']['input']  # Hex string of input data
            ))
        
        # If no valid transactions, raise an error
        if not txs:
            raise ValueError("No valid transactions found in forge output")
        
        # Get the next nonce if not provided
        nonce = fetch_next_nonce(self.safe_address) if nonce is None else nonce
        
        # Build the MultiSend transaction
        data = self.multisend.build_tx_data(txs)
        
        # Create the SafeTx using the safe-eth library
        safe_tx = self.safe.build_multisig_tx(
            self.multisend_address, 
            0, 
            data,
            operation=SafeOperationEnum.DELEGATE_CALL.value, 
            safe_nonce=nonce
        )
        
        return safe_tx

    def safe_tx_to_json(self, sender: str, safe_tx: SafeTx, signature: str = "") -> Dict[str, Any]:
        """
        Convert a SafeTx to the JSON format expected by the Safe API
        """
        # Ensure the signature has the 0x prefix
        if signature and not signature.startswith('0x'):
            signature = '0x' + signature
            
        return {
            'safe': self.safe_address,
            'to': safe_tx.to,
            'value': str(safe_tx.value),
            'data': safe_tx.data.hex(),
            'operation': safe_tx.operation,
            'gasToken': safe_tx.gas_token,
            'safeTxGas': str(safe_tx.safe_tx_gas),
            'baseGas': str(safe_tx.base_gas),
            'gasPrice': str(safe_tx.gas_price),
            'safeTxHash': safe_tx.safe_tx_hash.hex(),
            'refundReceiver': safe_tx.refund_receiver,
            'nonce': str(safe_tx.safe_nonce),
            'sender': sender,
            'signature': signature,
            'origin': 'fwrap-safe-script'
        }

def fetch_next_nonce(safe_address: str) -> int:
    """Fetch the next nonce for a Safe from the Safe Transaction Service API"""
    chain_id = os.getenv("CHAIN_ID", "1")
    url = f'https://safe-client.safe.global/v1/chains/{chain_id}/safes/{safe_address}/nonces'
    headers = {'Content-Type': 'application/json'}
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data['recommendedNonce']
    except Exception as e:
        raise Exception(f'Error fetching nonce: {str(e)}')

def checksum_address(address: str) -> str:
    """
    Implements EIP-55 address checksumming
    https://eips.ethereum.org/EIPS/eip-55
    Uses Keccak-256 as specified in Ethereum
    """
    # Normalize address
    if not address.startswith('0x'):
        address = '0x' + address
    
    # Remove 0x, pad to 40 hex chars if needed
    addr_without_prefix = address[2:].lower().rjust(40, '0')
    
    # Hash the address using Keccak-256
    hash_bytes = keccak(addr_without_prefix.encode('utf-8'))
    hash_hex = hash_bytes.hex()
    
    # Apply checksumming rules: uppercase if corresponding hash character >= 8
    checksum_addr = '0x'
    for i, char in enumerate(addr_without_prefix):
        if char in '0123456789':
            # Numbers are always lowercase
            checksum_addr += char
        else:
            # Letters are uppercase if corresponding hash digit >= 8
            if int(hash_hex[i], 16) >= 8:
                checksum_addr += char.upper()
            else:
                checksum_addr += char
    
    return checksum_addr

def sign_tx(safe_tx: SafeTx, account: str = None, password: str = None) -> str:
    """Sign a Safe transaction using cast wallet sign"""
    tx_hash_hex = safe_tx.safe_tx_hash.hex()
    print(f"Signing data: {tx_hash_hex}")
    
    cmd = ["cast", "wallet", "sign"]
    if account:
        cmd.extend(["--account", account])
    if password:
        cmd.extend(["--password", password])
    cmd.extend([f"{tx_hash_hex}", "--no-hash"])
    
    console.print(f"[yellow]Signing transaction with command: {' '.join(cmd)}[/yellow]")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        signature = result.stdout.strip()
        return signature
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error signing transaction: {e.stderr}[/red]")
        raise

def get_signer_address(account: str = None, password: str = None, is_hw_wallet: bool = False, mnemonic_index: int = None) -> str:
    """Get the address for an account using cast wallet address"""
    cmd = ["cast", "wallet", "address"]
    
    if account:
        cmd.extend(["--account", account])
    if is_hw_wallet:
        cmd.append("--ledger")
        if mnemonic_index is not None:
            cmd.extend(["--mnemonic-index", str(mnemonic_index)])
    if password:
        cmd.extend(["--unsafe-password", password])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error getting address: {e.stderr}")
        raise

def select_wallet() -> Tuple[str, str]:
    """
    List all available cast wallets and let the user select one.
    Returns a tuple of (wallet_name, wallet_address)
    """
    try:
        result = subprocess.run(
            ["cast", "wallet", "ls"],
            capture_output=True,
            text=True,
            check=True
        )
        # Parse the output to get a list of wallet names and addresses
        lines = result.stdout.strip().split('\n')
        wallets = []
        wallet_addresses = []
        
        # Skip the header line
        for line in lines[1:]:
            if line and not line.startswith('NAME'):
                parts = line.split()
                if len(parts) >= 2:  # Ensure we have both name and address
                    wallets.append(parts[0])
                    wallet_addresses.append(parts[1])
        
        if not wallets:
            console.print("[red]No wallets found.[/red]")
            console.print("Create a wallet first with: cast wallet new")
            raise ValueError("No wallets available")
        
        # Display wallets with numbers for selection
        console.print("[bold]Available wallets:[/bold]")
        for i, (wallet, address) in enumerate(zip(wallets, wallet_addresses)):
            console.print(f"  {i+1}. {wallet} - {address}")
        
        # Get user selection
        while True:
            try:
                selection = console.input("\nSelect a wallet (number): ")
                index = int(selection) - 1
                if 0 <= index < len(wallets):
                    return wallets[index], wallet_addresses[index]
                else:
                    console.print(f"[red]Invalid selection. Please enter a number between 1 and {len(wallets)}.[/red]")
            except ValueError:
                console.print("[red]Please enter a valid number.[/red]")
    
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error listing wallets: {e.stderr}[/red]")
        raise

def list_wallets() -> List[str]:
    """List all available cast wallets"""
    try:
        result = subprocess.run(
            ["cast", "wallet", "ls"],
            capture_output=True,
            text=True,
            check=True
        )
        # Parse the output to get a list of wallet names
        lines = result.stdout.strip().split('\n')
        wallets = []
        for line in lines:
            if line and not line.startswith('NAME'):  # Skip header
                parts = line.split()
                if parts:
                    wallets.append(parts[0])
        return wallets
    except subprocess.CalledProcessError as e:
        print(f"Error listing wallets: {e.stderr}")
        raise

def submit_safe_tx(tx_json: Dict[str, Any]) -> Dict[str, Any]:
    """Submit a Safe transaction to the Safe Transaction Service API"""
    chain_id = os.getenv("CHAIN_ID", "1")
    safe_address = tx_json['safe']
    url = f'https://safe-client.safe.global/v1/chains/{chain_id}/transactions/{safe_address}/propose'
    headers = {'Content-Type': 'application/json'}
    
    try:
        response = requests.post(url, headers=headers, json=tx_json)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise Exception(f'Error submitting transaction: {str(e)}')

def main():
    """Main entry point for the CLI"""
    parser = argparse.ArgumentParser(description='Run forge script and create Safe transaction')
    parser.add_argument('script_file', help='The script file to execute')
    parser.add_argument('--project-dir', default=os.getcwd(), help='Root directory of Forge project')
    parser.add_argument('--nonce', type=int, help='The nonce to use for the transaction')
    parser.add_argument('--account', help='The account to use for signing')
    parser.add_argument('--password', help='Password for the account')
    parser.add_argument('--rpc-url', help='RPC URL to use for running the script')
    parser.add_argument('--safe-address', help='Safe address to use')
    parser.add_argument('--sender-address', help='Address that will be shown as the transaction sender')
    parser.add_argument('--chain-id', default='1', help='Chain ID (default: 1 for Ethereum mainnet)')
    parser.add_argument('--debug', action='store_true', help='Debug mode: read from existing broadcast file')
    parser.add_argument('--broadcast-file', help='Path to broadcast file to use in debug mode')
    parser.add_argument('--dry-run', action='store_true', help='Generate the transaction but do not submit it')
    
    args = parser.parse_args()
    
    # Normalize script path
    script_path = args.script_file
    if not script_path.startswith('script/'):
        script_path = f"script/{script_path}"
    
    # Get environment variables or use args
    rpc_url = args.rpc_url or os.getenv("RPC_URL")
    safe_address = args.safe_address or os.getenv("SAFE_ADDRESS")
    sender_address = args.sender_address or os.getenv("SAFE_PROPOSER")
    chain_id = args.chain_id or os.getenv("CHAIN_ID", "1")
    
    # Set environment variables for the API
    os.environ["CHAIN_ID"] = chain_id
    
    # Validate required parameters
    if not rpc_url:
        print("Error: RPC_URL environment variable or --rpc-url not set")
        return 1
    
    if not safe_address:
        print("Error: SAFE_ADDRESS environment variable or --safe-address not set")
        return 1
    
    if not sender_address:
        print("Error: SAFE_PROPOSER environment variable or --sender-address not set")
        return 1
    
    # Initialize our classes
    forge_runner = ForgeScriptRunner(rpc_url, args.project_dir)
    safe_builder = SafeTransactionBuilder(safe_address, rpc_url)
    
    # Get transaction data
    if args.debug:
        if not args.broadcast_file:
            # Try to find the latest broadcast file
            script_name = Path(script_path).name
            broadcast_file = Path(args.project_dir) / "broadcast" / script_name / "1" / "dry-run" / "run-latest.json"
            if not broadcast_file.exists():
                print(f"Error: No broadcast file found at {broadcast_file}")
                print("Please specify --broadcast-file or run without --debug")
                return 1
        else:
            broadcast_file = Path(args.broadcast_file)
            if not broadcast_file.exists():
                print(f"Error: Broadcast file not found: {broadcast_file}")
                return 1
        
        print(f"Reading from broadcast file: {broadcast_file}")
        with open(broadcast_file) as f:
            json_data = json.load(f)
            success = True
    else:
        # Run forge script normally
        success, json_data, error = forge_runner.run_forge_script(script_path)
        if not success:
            print(f"Error: {error}")
            return 1
    
    try:
        # Build Safe transaction
        safe_tx = safe_builder.build_safe_tx(json_data, args.nonce)
        
        # Sign the transaction if an account is provided
        signature = ""
        if args.account:
            signature = sign_tx(safe_tx, args.account, args.password)
        
        # Create the transaction JSON
        tx_json = safe_builder.safe_tx_to_json(sender_address, safe_tx, signature=signature)
        
        # Print the transaction
        print("\nSafe Transaction:")
        print(json.dumps(tx_json, indent=2))
        
        # Submit the transaction unless dry run
        if not args.dry_run:
            if signature:
                result = submit_safe_tx(tx_json)
                print(f"\nTransaction submitted successfully with nonce {safe_tx.safe_nonce}")
                print(f"Safe transaction hash: {safe_tx.safe_tx_hash.hex()}")
                return 0
            else:
                print("\nNo signature provided, transaction not submitted")
                print(f"Safe transaction hash: {safe_tx.safe_tx_hash.hex()}")
                return 0
        else:
            print("\nDry run, transaction not submitted")
            print(f"Safe transaction hash: {safe_tx.safe_tx_hash.hex()}")
            return 0
            
    except Exception as e:
        print(f"Error: {str(e)}")
        return 1

def fwrap_safe_command(script_path: str, project_dir: str = None, account: str = None, 
                      password: str = None, rpc_url: str = None, safe_address: str = None,
                      sender_address: str = None, dry_run: bool = False):
    """
    Integration function for fwrap to use safe functionality programmatically.
    Returns a tuple of (success, tx_hash, error_message)
    """
    try:
        # Ensure we have the required parameters
        rpc_url = rpc_url or os.getenv("RPC_URL")
        safe_address = safe_address or os.getenv("SAFE_ADDRESS")
        sender_address = sender_address or os.getenv("SAFE_PROPOSER")
        
        if not rpc_url or not safe_address or not sender_address:
            missing = []
            if not rpc_url:
                missing.append("RPC_URL")
            if not safe_address:
                missing.append("SAFE_ADDRESS")
            if not sender_address:
                missing.append("SAFE_PROPOSER")
                
            return (False, None, f"Missing required parameters: {', '.join(missing)}. "
                               f"Set these in your global config at {GLOBAL_CONFIG_PATH}")
        
        # Run the forge script
        forge_runner = ForgeScriptRunner(rpc_url, project_dir or os.getcwd())
        safe_builder = SafeTransactionBuilder(safe_address, rpc_url)
        
        success, json_data, error = forge_runner.run_forge_script(script_path)
        if not success:
            return (False, None, f"Error running forge script: {error}")
        
        # Build the safe transaction
        safe_tx = safe_builder.build_safe_tx(json_data)
        
        # Sign if account provided
        signature = ""
        if not account:
            account_name, address = select_wallet()
            signature = sign_tx(safe_tx, account_name, password)
        else:
            signature = sign_tx(safe_tx, account, password)

        # Create the transaction JSON
        tx_json = safe_builder.safe_tx_to_json(sender_address, safe_tx, signature=signature)
        
        # Submit the transaction unless dry run
        if not dry_run and signature:
            submit_safe_tx(tx_json)
        
        return (True, safe_tx.safe_tx_hash.hex(), None)
        
    except Exception as e:
        return (False, None, str(e))

if __name__ == "__main__":
    try:
        sys.exit(main())
    except ImportError:
        console.print("[red]Error:[/red] Safe dependencies not installed")
        console.print("Install with: pip install 'fwrap[safe]'")
        sys.exit(1)
