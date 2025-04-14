"""
Safe Transaction Management

This module provides functionality to create and sign Gnosis Safe transactions
from Foundry forge script executions.
"""

import json
import os
import time
from typing import Optional, Dict, Any, NamedTuple, Tuple, List
from pathlib import Path
import sys
import asyncio
from asyncio.subprocess import Process
import requests
from eth_hash.auto import keccak
from safe_eth.safe import Safe
from safe_eth.eth import EthereumClient
from safe_eth.safe.safe import SafeV111, SafeV120, SafeV130, SafeV141
from safe_eth.safe.enums import SafeOperationEnum
from safe_eth.safe.multi_send import MultiSend, MultiSendOperation, MultiSendTx
from safe_eth.safe.safe_tx import SafeTx
from safesmith.settings import GLOBAL_CONFIG_PATH, load_settings
from safesmith.cast import (
    sign_transaction, 
    sign_typed_data,
    get_address,
    select_wallet, 
    WalletError
)
from safesmith.errors import handle_errors, SafeError, NetworkError, ScriptError, result_or_raise
from rich.console import Console
import time
from eth_account import Account
from eth_account.messages import encode_typed_data
from rich.status import Status
from rich.spinner import Spinner

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
    safe_nonce: int = None
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

    @handle_errors(error_type=SafeError)
    async def _run_forge_script_async(self, script_path: str) -> Dict[str, Any]:
        """Run forge script asynchronously and capture output"""
        # Disable traceback printing
        orig_tracebacklimit = getattr(sys, 'tracebacklimit', 1000)
        sys.tracebacklimit = 0
        
        try:
            command = [
                "forge", "script",
                script_path,
                "--rpc-url", self.rpc_url,
                "--slow",
                "-vvv"
            ]
            
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
                raise SafeError(f"Forge script failed with return code {return_code}")

            # Find and parse the latest run JSON file
            latest_run = self._find_latest_run_json(script_path)
            
            if latest_run:
                with open(latest_run) as f:
                    return json.load(f)
            
            raise SafeError(
                "Could not find last run data."
            )
        finally:
            # Restore original tracebacklimit
            sys.tracebacklimit = orig_tracebacklimit

    def run_forge_script(self, script_path: str) -> Dict[str, Any]:
        """Runs forge script and returns json_data"""
        return asyncio.run(self._run_forge_script_async(script_path))

    def _find_latest_run_json(self, script_path: Path) -> Optional[Path]:
        """Find the latest run-*.json file in the directory"""
        broadcast_dir = self.project_root / "broadcast"
        script_name = Path(script_path).name
        path = broadcast_dir / script_name / "1" / "dry-run"
        if not path.exists():
            return None
        
        json_files = list(path.glob("run-*.json"))
        if not json_files:
            return None
        result = max(json_files, key=lambda x: x.stat().st_mtime)
        return result

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
        
    @handle_errors(error_type=SafeError)
    def build_safe_tx(self, nonce: int, forge_output: Dict[str, Any]) -> SafeTx:
        """
        Builds Safe transaction from forge output
        Batches all transactions through the MultiSend contract
        """
        # Extract transactions from forge output
        txs = []
        for tx in forge_output["transactions"]:
            # Skip transactions to the console logger
            if not 'to' in tx['transaction']:
                raise SafeError("Cannot create Safe transaction: Missing 'to' field. Safes cannot deploy contracts.")
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
            raise SafeError("No valid transactions found in forge output")
        
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

    @handle_errors(error_type=SafeError)
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
            'origin': 'safesmith-script'
        }

@handle_errors(error_type=NetworkError)
def fetch_next_nonce(safe_address: str, chain_id: str = "1") -> int:
    """
    Fetch the next nonce for a Safe from the Safe Transaction Service API
    
    Args:
        safe_address: The address of the Safe
        chain_id: The chain ID (default: "1" for Ethereum mainnet)
    
    Returns:
        The recommended nonce for the Safe
    """
    url = f'https://safe-client.safe.global/v1/chains/{chain_id}/safes/{safe_address}/nonces'
    headers = {'Content-Type': 'application/json'}
    
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
    return data['recommendedNonce']

@handle_errors(error_type=SafeError)
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

@handle_errors(error_type=SafeError)
def sign_tx(safe_tx: SafeTx, proposer: str = None, password: str = None) -> str:
    """Sign a Safe transaction using cast wallet sign"""
    tx_hash_hex = safe_tx.safe_tx_hash.hex()   
    console.print(f"Signing transaction with {proposer}", markup=False)
    
    # Use the cast.py helper function directly
    try:
        return sign_transaction(
            tx_hash=tx_hash_hex,
            account=proposer,
            password=password,
            no_hash=True
        )
    except WalletError as e:
        raise SafeError(f"Error signing transaction: {str(e)}")

@handle_errors(error_type=SafeError)
def get_proposer_address(proposer: str = None, password: str = None, is_hw_wallet: bool = False, mnemonic_index: int = None) -> str:
    """Get the address for an account using cast wallet address"""
    try:
        return get_address(
            account=proposer,
            password=password,
            is_hw_wallet=is_hw_wallet,
            mnemonic_index=mnemonic_index
        )
    except WalletError as e:
        raise SafeError(f"Error getting proposer address: {str(e)}")

@handle_errors(error_type=NetworkError)
def submit_safe_tx(tx_json: Dict[str, Any], chain_id: str = "1") -> Dict[str, Any]:
    """
    Submit a Safe transaction to the Safe Transaction Service API
    
    Args:
        tx_json: The transaction JSON to submit
        chain_id: The chain ID (default: "1" for Ethereum mainnet)
    
    Returns:
        The response JSON from the API
    """
    safe_address = tx_json['safe']
    url = f'https://safe-client.safe.global/v1/chains/{chain_id}/transactions/{safe_address}/propose'
    headers = {'Content-Type': 'application/json'}
    
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

def process_safe_transaction(
    script_path: str,
    rpc_url: str,
    safe_address: str,
    nonce: int,
    project_dir: str = None,
    proposer: str = None,
    proposer_alias: str = None,
    password: str = None,
    chain_id: str = "1",
    post: bool = False,
    skip_broadcast_check: bool = False
) -> Tuple[str, Dict[str, Any]]:
    """
    Core implementation of Safe transaction processing logic.
    Returns (tx_hash, tx_json)
    """
    # Initialize our classes
    forge_runner = ForgeScriptRunner(rpc_url, project_dir or os.getcwd())
    safe_builder = SafeTransactionBuilder(safe_address, rpc_url)
    
    # Run forge script
    json_data = forge_runner.run_forge_script(script_path)
    
    # Check timestamp if skip_broadcast_check is enabled
    if skip_broadcast_check and "timestamp" in json_data:
        current_time = int(time.time())
        script_time = int(json_data["timestamp"])
        time_diff = abs(current_time - script_time)
        
        if time_diff > 30:
            # Format times for display
            from datetime import datetime
            script_time_str = datetime.fromtimestamp(script_time).strftime('%Y-%m-%d %H:%M:%S')
            current_time_str = datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S')
            
            console.print(f"[yellow]WARNING: Script timestamp differs significantly from current time![/yellow]")
            console.print(f"[yellow]Script time: {script_time_str} ({script_time})[/yellow]")
            console.print(f"[yellow]Current time: {current_time_str} ({current_time})[/yellow]")
            console.print(f"[yellow]Difference: {time_diff} seconds[/yellow]")
            console.print("[yellow]This could indicate the data you are attempting to post is not what you expect.[/yellow]")
            
            import click
            if not click.confirm("Continue with potentially stale transaction data?", default=False):
                raise SafeError("Transaction aborted by user due to timestamp discrepancy")
    
    # Build Safe transaction
    safe_tx = safe_builder.build_safe_tx(nonce, json_data)
    
    # If no proposer specified, prompt for wallet selection
    if post:
        if not proposer:
            console.print(f"\n[yellow]Please select a proposer wallet...[/yellow]")
            try:
                proposer_alias = select_wallet()
                console.print(f"Selected {proposer_alias}")
                proposer = get_proposer_address(proposer_alias, password)
            except (WalletError, SafeError) as e:
                raise SafeError(f"Error selecting wallet: {str(e)}")
        elif not proposer and proposer_alias:
            console.print(f"\n[yellow]Please select the wallet alias for your set proposer: {proposer}...[/yellow]")
            try:
                proposer_alias = select_wallet()
            except WalletError as e:
                raise SafeError(f"Error selecting wallet: {str(e)}")
    
        signature = sign_tx(safe_tx, proposer_alias, password)
        tx_json = safe_builder.safe_tx_to_json(proposer, safe_tx, signature=signature)
        tx_hash = safe_tx.safe_tx_hash.hex()
        if post:
            if not signature:
                raise SafeError("Cannot post transaction without a signature")
            submit_safe_tx(tx_json, chain_id)
            console.print(f"\n[green]Safe transaction {nonce} created successfully![/green]")
            console.print(f"View it here: https://app.safe.global/transactions/queue?safe={safe_address}")
    else:
        tx_hash = None
        tx_json = None
        console.print("[yellow]This was a dry run. To submit the transaction use --post.[/yellow]")
        
    return tx_hash, tx_json

@handle_errors(error_type=NetworkError)
def get_chain_id_from_rpc(rpc_url: str) -> str:
    """
    Get the chain ID from the RPC endpoint
    
    Args:
        rpc_url: The RPC URL to query
        
    Returns:
        The chain ID as a string
    """
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_chainId",
        "params": [],
        "id": 1
    }
    
    response = requests.post(rpc_url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    
    if "error" in data:
        raise NetworkError(f"RPC error: {data['error']['message']}")
    
    # Convert hex chain ID to decimal string
    try:
        chain_id = str(int(data["result"], 16))
        return chain_id
    except (ValueError, KeyError) as e:
        raise NetworkError(f"Invalid response from RPC endpoint: {str(e)}")

def run_command(script_path: str, project_dir: str = None, proposer: str = None, proposer_alias: str = None,
                password: str = None, rpc_url: str = None, safe_address: str = None, post: bool = False,
                nonce: int = None, chain_id: str = None, skip_broadcast_check: bool = False) -> Tuple[str, Dict[str, Any]]:
    """
    Integration function for safesmith to use safe functionality programmatically.
    Returns a tuple of (tx_hash, tx_json)
    """
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
        raise SafeError(f"Missing required parameters: {', '.join(missing)}. "
                       f"Set these in your global config or provide directly.")
    
    # Get chain_id from RPC if not provided
    if not chain_id:
        try:
            chain_id = get_chain_id_from_rpc(effective_rpc_url)
        except Exception as e:
            console.print(f"[yellow]Warning: Could not determine chain_id from RPC: {str(e)}[/yellow]")
            console.print("[yellow]Defaulting to chain_id=1 (Ethereum mainnet)[/yellow]")
            chain_id = "1"
    
    # Process the transaction
    return process_safe_transaction(
        script_path=script_path,
        rpc_url=effective_rpc_url,
        safe_address=safe_address,
        nonce=nonce,
        project_dir=project_dir,
        proposer=proposer,
        proposer_alias=proposer_alias,
        password=password,
        post=post,
        chain_id=chain_id,
        skip_broadcast_check=skip_broadcast_check
    )

def generate_totp():
    """Generate a time-based one-time password for delete request authentication"""
    return int(time.time()) // 3600

@handle_errors(error_type=SafeError, log_error=False)
def sign_delete_request(safe_tx_hash: str, account: str, password: Optional[str] = None, safe_address: str = None, chain_id: int = 1) -> Tuple[int, str]:
    """
    Sign a delete request for a Safe transaction using EIP-712 typed data
    
    Args:
        safe_tx_hash: Hash of the Safe transaction to delete
        account: Account alias to use for signing
        password: Password for the wallet (optional)
        safe_address: Address of the Safe
        chain_id: Chain ID of the network
    
    Returns:
        Tuple of (totp, signature)
    """
    if not safe_address:
        raise SafeError("Safe address is required for EIP-712 signing")
    
    totp = generate_totp()
    console.print(f"Signing delete request for selected transaction.")
    
    # Ensure safe_tx_hash has 0x prefix
    if not safe_tx_hash.startswith('0x'):
        safe_tx_hash = '0x' + safe_tx_hash
    
    # Create EIP-712 structured data
    structured_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "DeleteRequest": [
                {"name": "safeTxHash", "type": "bytes32"},
                {"name": "totp", "type": "uint256"},
            ]
        },
        "domain": {
            "name": "Safe Transaction Service",
            "version": "1.0",
            "chainId": chain_id,
            "verifyingContract": safe_address,
        },
        "primaryType": "DeleteRequest",
        "message": {
            # The hash doesn't need to be converted to bytes for the Cast wallet
            "safeTxHash": safe_tx_hash,
            "totp": totp,
        }
    }
    
    # Use the cast.py helper function to sign EIP-712 data
    try:
        signature = sign_typed_data(
            typed_data=structured_data,
            account=account,
            password=password
        )
    except WalletError as e:
        raise SafeError(f"Error signing delete request: {str(e)}")
    
    return totp, signature

@handle_errors(error_type=NetworkError, log_error=False)
def fetch_safe_transaction_by_nonce(safe_address: str, nonce: int, chain_id: int = 1) -> Optional[str]:
    """
    Fetch a Safe transaction by nonce
    
    Returns:
        The safeTxHash if found, None otherwise
    """
    # Use the correct URL format based on chain_id
    if chain_id == 1:
        base_url = "https://safe-transaction-mainnet.safe.global"
    else:
        raise SafeError("Only chain ID 1 (mainnet) is supported for Safe transaction deletion at this time")
    
    url = f"{base_url}/api/v2/safes/{safe_address}/multisig-transactions/"
    headers = {"Content-Type": "application/json"}
    
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
    
    # Filter for pending transactions with matching nonce
    for tx in data.get('results', []):
        if isinstance(tx, dict) and 'nonce' in tx:
            if int(tx['nonce']) == nonce and not tx.get('isExecuted', False):
                return tx.get('safeTxHash')
    
    return None

@handle_errors(error_type=SafeError, log_error=False)
def delete_safe_transaction(safe_tx_hash: str, safe_address: str, nonce: int, account: str, password: Optional[str] = None, chain_id: int = 1) -> None:
    """
    Delete a Safe transaction by nonce
    
    Args:
        safe_tx_hash: The hash of the transaction to delete
        safe_address: Address of the Safe
        nonce: Nonce of the transaction to delete
        account: Account alias to use for signing
        password: Optional password for the wallet
        chain_id: Chain ID of the network
    """
    if not safe_address:
        raise SafeError("Safe address is required for deletion")
    
    # Get the TOTP and signature for the delete request
    totp, signature = sign_delete_request(
        safe_tx_hash=safe_tx_hash,
        account=account,
        password=password,
        safe_address=safe_address,
        chain_id=chain_id
    )
    
    # Use the correct URL format based on chain_id
    if chain_id == 1:
        base_url = "https://safe-transaction-mainnet.safe.global"
    else:
        # For other networks like Goerli, Sepolia, etc.
        network_name = "goerli" if chain_id == 5 else f"chain-{chain_id}"
        base_url = f"https://safe-transaction-{network_name}.safe.global"
    
    endpoint = f"/api/v2/multisig-transactions/{safe_tx_hash}/"
    url = base_url + endpoint
    
    headers = {"Content-Type": "application/json"}
    data = json.dumps({"signature": signature, "totp": totp})
    
    # Use Rich spinner to show progress during API call
    spinner_text = f"[bold blue]Sending delete request for transaction with nonce {nonce}[/bold blue]"
    with console.status(spinner_text, spinner="dots") as status:
        try:
            # Add timeout to prevent hanging
            response = requests.delete(url, headers=headers, data=data, timeout=30)
            
            # Update spinner with response status
            if 200 <= response.status_code < 300:
                status.update(f"[green]Request successful! Response code: {response.status_code}[/green]")
            else:
                status.update(f"[yellow]Request received code: {response.status_code}[/yellow]")
                
                # Check for error response
                if response.status_code >= 400:
                    error_msg = f"Failed to delete transaction: HTTP {response.status_code}"
                    try:
                        error_data = response.json()
                        if isinstance(error_data, dict) and "message" in error_data:
                            error_msg = f"Failed to delete transaction: {error_data['message']}"
                    except:
                        pass  # Use the default error message
                    
                    raise NetworkError(error_msg)
                
                response.raise_for_status()
        except requests.exceptions.Timeout:
            raise NetworkError("Request timed out while trying to delete the transaction")
        except requests.exceptions.RequestException as e:
            # Simplified error with just the main message, no stack trace
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                error_msg = f"HTTP error {e.response.status_code}"
                
            raise NetworkError(f"Network error: {error_msg}")