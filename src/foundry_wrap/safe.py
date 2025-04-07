"""
Safe Transaction Management for foundry-wrap

This module provides functionality to create and sign Gnosis Safe transactions
from Foundry forge script executions.
"""

import json
import os
from typing import Optional, Dict, Any, NamedTuple, Tuple, List
from pathlib import Path
import sys
import asyncio
from asyncio.subprocess import Process
import subprocess  # Keep for ForgeScriptRunner
import requests
import argparse
from eth_hash.auto import keccak
from safe_eth.safe import Safe
from safe_eth.eth import EthereumClient
from safe_eth.safe.safe import SafeV111, SafeV120, SafeV130, SafeV141
from safe_eth.safe.enums import SafeOperationEnum
from safe_eth.safe.multi_send import MultiSend, MultiSendOperation, MultiSendTx
from safe_eth.safe.safe_tx import SafeTx
from safe_eth.safe.signatures import signature_split, signature_to_bytes
from safe_eth.safe.api import TransactionServiceApi
from safe_eth.safe.safe_signature import SafeSignature
from foundry_wrap.settings import GLOBAL_CONFIG_PATH, FoundryWrapSettings, load_settings, RpcSettings, SafeSettings
# Import the cast helper functions with aliases to avoid naming conflicts
from foundry_wrap.cast import (
    sign_transaction, 
    get_address, 
    list_wallets, 
    select_wallet, 
    CastError
)
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
            if not 'to' in tx['transaction']:
                raise ValueError("Cannot create Safe transaction: Missing 'to' field. Safes cannot deploy contracts.")
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

    def safe_tx_to_json(self, signer_address: str, safe_tx: SafeTx, signature: str = "") -> Dict[str, Any]:
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
            'data': '0x' + safe_tx.data.hex().replace('0x', ''),
            'operation': safe_tx.operation,
            'gasToken': safe_tx.gas_token,
            'safeTxGas': str(safe_tx.safe_tx_gas),
            'baseGas': str(safe_tx.base_gas),
            'gasPrice': str(safe_tx.gas_price),
            'safeTxHash': '0x' + safe_tx.safe_tx_hash.hex().replace('0x', ''),
            'refundReceiver': safe_tx.refund_receiver,
            'nonce': str(safe_tx.safe_nonce),
            'sender': signer_address,
            'signature': signature,
            'origin': 'foundry-wrap-safe-script'
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

def sign_tx(safe_tx: SafeTx, proposer: str = None, password: str = None) -> str:
    """Sign a Safe transaction using cast wallet sign"""
    tx_hash_hex = safe_tx.safe_tx_hash.hex()   
    console.print(f"Signing transaction with {proposer}...")
    try:
        # Use the cast.py helper function
        signature = sign_transaction(
            tx_hash=tx_hash_hex,
            account=proposer,
            password=password,
            no_hash=True
        )
        return signature
    except CastError as e:
        console.print(f"[red]Error signing transaction: {str(e)}[/red]")
        raise

def get_proposer_address(proposer: str = None, password: str = None, is_hw_wallet: bool = False, mnemonic_index: int = None) -> str:
    """Get the address for an account using cast wallet address"""
    try:
        return get_address(
            account=proposer,
            password=password,
            is_hw_wallet=is_hw_wallet,
            mnemonic_index=mnemonic_index
        )
    except CastError as e:
        console.print(f"[red]Error getting address: {str(e)}[/red]")
        raise


def submit_safe_tx(tx_json: Dict[str, Any]) -> Dict[str, Any]:
    """Submit a Safe transaction to the Safe Transaction Service API"""
    chain_id = os.getenv("CHAIN_ID", "1")
    safe_address = tx_json['safe']
    url = f'https://safe-client.safe.global/v1/chains/{chain_id}/transactions/{safe_address}/propose'
    headers = {'Content-Type': 'application/json'}
    
    try:
        print("Submitting transaction to Safe API...")
        
        response = requests.post(url, headers=headers, json=tx_json)
        
        # Don't raise exception yet to capture error response
        if response.status_code >= 400:
            print(f"Error response ({response.status_code}):")
            print(f"Response headers: {dict(response.headers)}")
            try:
                error_details = response.json()
                print(f"Error details: {json.dumps(error_details, indent=2)}")
            except:
                print(f"Raw response: {response.text}")
            response.raise_for_status()  # Now raise the exception
        return response.json()
    except Exception as e:
        print(f"Exception details: {type(e).__name__}: {str(e)}")
        raise Exception(f'Error submitting transaction: {str(e)}')

def process_safe_transaction(
    script_path: str,
    rpc_url: str,
    safe_address: str,
    project_dir: str = None,
    nonce: int = None,
    proposer: str = None,
    password: str = None,
    chain_id: str = "1",
    dry_run: bool = False,
    broadcast_file: str = None,
    debug_mode: bool = False
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Core implementation of Safe transaction processing logic.
    Returns (success, tx_hash, error_message)
    """
    try:
        # Set environment variables for the API
        os.environ["CHAIN_ID"] = chain_id
        
        # Initialize our classes
        forge_runner = ForgeScriptRunner(rpc_url, project_dir or os.getcwd())
        safe_builder = SafeTransactionBuilder(safe_address, rpc_url)
        
        # Get transaction data
        if debug_mode:
            if not broadcast_file:
                # Try to find the latest broadcast file
                script_name = Path(script_path).name
                broadcast_file = Path(project_dir or os.getcwd()) / "broadcast" / script_name / "1" / "dry-run" / "run-latest.json"
                if not broadcast_file.exists():
                    return False, None, f"No broadcast file found at {broadcast_file}"
            else:
                broadcast_file = Path(broadcast_file)
                if not broadcast_file.exists():
                    return False, None, f"Broadcast file not found: {broadcast_file}"
            
            with open(broadcast_file) as f:
                json_data = json.load(f)
                success = True
        else:
            # Run forge script normally
            success, json_data, error = forge_runner.run_forge_script(script_path)
            if not success:
                return False, None, f"Error running forge script: {error}"
        
        # Build Safe transaction
        safe_tx = safe_builder.build_safe_tx(json_data, nonce)
        
        # Get the proposer
        proposer_address = None
        
        # If no proposer specified, prompt for wallet selection
        if not proposer:
            console.print(f"\n[yellow]Please select a proposer wallet...[/yellow]")
            wallet_name = select_wallet()
            console.print(f"Selected {wallet_name}")
            proposer = wallet_name
            proposer_address = get_address(account=proposer, password=password)
        else:
            console.print(f"\n[yellow]Please select the wallet for your set proposer: {proposer}...[/yellow]")
            wallet_name = select_wallet()
        
        
        # Sign the transaction
        signature = ""
        if not dry_run:
            try:
                signature = sign_tx(safe_tx, proposer, password)
            except CastError as e:
                return False, None, f"Error signing transaction: {str(e)}"
        
        # Create the transaction JSON
        tx_json = safe_builder.safe_tx_to_json(proposer_address, safe_tx, signature=signature)
        
        # Format transaction hash
        tx_hash = safe_tx.safe_tx_hash.hex()
        
        # Submit the transaction unless dry run
        if not dry_run and signature:
            submit_safe_tx(tx_json)
            
        return True, tx_hash, tx_json
        
    except Exception as e:
        return False, None, str(e)

def main():
    """Main entry point for the CLI"""
    parser = argparse.ArgumentParser(description='Run forge script and create Safe transaction')
    parser.add_argument('script_file', help='The script file to execute')
    parser.add_argument('--project-dir', default=os.getcwd(), help='Root directory of Forge project')
    parser.add_argument('--nonce', type=int, help='The nonce to use for the transaction')
    parser.add_argument('--proposer', help='The proposer to use for signing')
    parser.add_argument('--password', help='Password for the account')
    parser.add_argument('--rpc-url', help='RPC URL to use for running the script')
    parser.add_argument('--safe-address', help='Safe address to use')
    parser.add_argument('--chain-id', default='1', help='Chain ID (default: 1 for Ethereum mainnet)')
    parser.add_argument('--debug', action='store_true', help='Debug mode: read from existing broadcast file')
    parser.add_argument('--broadcast-file', help='Path to broadcast file to use in debug mode')
    parser.add_argument('--dry-run', action='store_true', help='Generate the transaction but do not submit it')
    
    args = parser.parse_args()
    
    # Normalize script path
    script_path = args.script_file
    if not script_path.startswith('script/'):
        script_path = f"script/{script_path}"
    
    # Load settings with CLI options
    cli_options = {
        "rpc.url": args.rpc_url,
        "safe.safe_address": args.safe_address,
        "safe.proposer": args.proposer,
    }
    
    # Only include non-None options
    cli_options = {k: v for k, v in cli_options.items() if v is not None}
    
    # Load settings from various sources
    settings = load_settings(cli_options=cli_options)
    
    # Extract settings into variables
    rpc_url = settings.rpc.url
    safe_address = settings.safe.safe_address
    proposer = settings.safe.proposer
    chain_id = args.chain_id or os.getenv("CHAIN_ID", "1")  # Chain ID not part of settings model
    
    # Validate required parameters
    missing = []
    if not rpc_url:
        missing.append("RPC URL")
    if not safe_address:
        missing.append("Safe address")
    
    if missing:
        console.print(f"[red]Error: Missing required parameters: {', '.join(missing)}[/red]")
        console.print(f"You can set these values in your global config at {GLOBAL_CONFIG_PATH}")
        console.print("Run the following command to configure:")
        if "RPC URL" in missing:
            console.print(f"  foundry-wrap config --global --rpc-url <YOUR_RPC_URL>")
        if "Safe address" in missing:
            console.print(f"  foundry-wrap config --global --safe-address <YOUR_SAFE_ADDRESS>")
        return 1
    
    try:
        # Process the transaction
        success, tx_hash, result = process_safe_transaction(
            script_path=script_path,
            rpc_url=rpc_url,
            safe_address=safe_address,
            project_dir=args.project_dir,
            nonce=args.nonce,
            proposer=proposer,
            password=args.password,
            chain_id=chain_id,
            dry_run=args.dry_run,
            broadcast_file=args.broadcast_file,
            debug_mode=args.debug
        )
        
        if success:
            return 0
        else:
            console.print(f"[red]Error: {result}[/red]")
            return 1
            
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        return 1

def run_command(script_path: str, project_dir: str = None, proposer: str = None, 
                password: str = None, rpc_url: str = None, safe_address: str = None, dry_run: bool = False):
    """
    Integration function for foundry-wrap to use safe functionality programmatically.
    Returns a tuple of (success, tx_hash, error_message)
    """
    try:
        # Use the directly provided values - no settings loading
        # Default rpc_url if not provided
        effective_rpc_url = rpc_url or "https://eth.merkle.io"
        
        # Validate required parameters
        missing = []
        if not effective_rpc_url:
            missing.append("RPC URL")
        if not safe_address:
            missing.append("Safe address")
        
        if missing:
            return (False, None, f"Missing required parameters: {', '.join(missing)}. "
                               f"Set these in your global config or provide directly.")
        
        # Process the transaction with direct values
        success, tx_hash, result = process_safe_transaction(
            script_path=script_path,
            rpc_url=effective_rpc_url,
            safe_address=safe_address,
            project_dir=project_dir,
            proposer=proposer,
            password=password,
            dry_run=dry_run
        )
        
        if success:
            return (True, tx_hash, None)
        else:
            return (False, None, result)
    
    except ImportError as e:
        # Get the specific missing module
        missing_module = str(e).split("'")[1] if "'" in str(e) else "unknown"
        
        error_msg = (
            f"Missing Safe dependency: '{missing_module}'\n"
            "The Safe transaction functionality requires additional packages.\n"
            "Install with: pip install 'foundry-wrap[safe]'\n\n"
        )
        
        if missing_module == "safe_eth.safe":
            error_msg += "Required package: 'safe-eth-py' is missing."
        elif missing_module.startswith("eth_"):
            error_msg += f"Required Ethereum dependency '{missing_module}' is missing."
        elif missing_module == "web3":
            error_msg += "Required 'web3' package for Ethereum interactions is missing."
        
        return (False, None, error_msg)
    
    except Exception as e:
        return (False, None, str(e))

if __name__ == "__main__":
    try:
        sys.exit(main())
    except ImportError as e:
        # Get the specific missing module
        missing_module = str(e).split("'")[1] if "'" in str(e) else "unknown"
        
        console.print(f"[red]Error:[/red] Safe dependencies not installed - missing module: [bold]{missing_module}[/bold]")
        console.print("This functionality requires the Gnosis Safe packages and related dependencies.")
        console.print("\nTo fix, install the safe extras:")
        console.print("    pip install 'foundry-wrap[safe]'")
        
        if missing_module == "safe_eth.safe":
            console.print("\nSpecifically missing the 'safe-eth-py' package.")
        elif missing_module.startswith("eth_"):
            console.print(f"\nSpecifically missing the '{missing_module}' Ethereum dependency.")
        elif missing_module == "web3":
            console.print("\nSpecifically missing the 'web3' package for Ethereum interactions.")
        
        # Print additional debugging info if module is unexpected
        if not any(m in missing_module for m in ["safe_eth", "eth_", "web3"]):
            console.print(f"\nUnexpected missing module. Please file a bug report with this info:")
            import traceback
            console.print(traceback.format_exc())
            
        sys.exit(1)
