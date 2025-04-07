"""
Foundry Cast CLI Helper

This module wraps interactions with the Ethereum toolchain's `cast` command line utility,
providing a cleaner interface and better error handling for working with wallets,
signing transactions, and other cast functions.
"""

import subprocess
import sys
import json
from typing import List, Tuple, Optional, Dict, Any, Union
from pathlib import Path
from rich.console import Console

# Create a console instance for rich output
console = Console()

class CastError(Exception):
    """Exception raised for errors in cast operations."""
    pass

def run_cast_command(args: List[str], capture_output: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    """
    Run a cast command with proper error handling
    
    Args:
        args: List of command arguments (without the initial 'cast')
        capture_output: Whether to capture the command output
        check: Whether to check for successful return code
        
    Returns:
        CompletedProcess instance
    
    Raises:
        CastError: If the command fails and check is True
    """
    cmd = ["cast"] + args
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=check
        )
        return result
    except subprocess.CalledProcessError as e:
        error_msg = f"Cast command failed: {e.stderr}"
        if check:
            raise CastError(error_msg) from e
        console.print(f"[yellow]Warning: {error_msg}[/yellow]")
        return e

# Wallet Management Functions

def sign_transaction(tx_hash: str, account: Optional[str] = None, 
                     password: Optional[str] = None, no_hash: bool = True) -> str:
    """
    Sign a transaction hash using cast wallet sign
    
    Args:
        tx_hash: The transaction hash to sign (with or without 0x prefix)
        account: Optional account name to use for signing
        password: Optional password for the account
        no_hash: Whether to use the --no-hash flag (default: True)
        
    Returns:
        The signature as a string
    
    Raises:
        CastError: If signing fails
    """
    # Ensure tx_hash has 0x prefix if it doesn't already
    if not tx_hash.startswith('0x'):
        tx_hash = '0x' + tx_hash
        
    cmd = ["wallet", "sign"]
    
    if account:
        cmd.extend(["--account", account])
    if password:
        cmd.extend(["--password", password])
    if no_hash:
        cmd.append("--no-hash")
        
    cmd.append(tx_hash)
    
    try:
        result = run_cast_command(cmd)
        return result.stdout.strip()
    except CastError as e:
        console.print(f"[red]Error signing transaction: {str(e)}[/red]")
        raise

def get_address(account: Optional[str] = None, password: Optional[str] = None, 
                is_hw_wallet: bool = False, mnemonic_index: Optional[int] = None) -> str:
    """
    Get the address for a wallet account using cast wallet address
    
    Args:
        account: Optional account name to get address for
        password: Optional password for the account
        is_hw_wallet: Whether this is a hardware wallet (Ledger)
        mnemonic_index: Optional mnemonic index for hardware wallets
        
    Returns:
        The wallet address as a string
    
    Raises:
        CastError: If getting the address fails
    """
    cmd = ["wallet", "address"]
    
    if account:
        cmd.extend(["--account", account])
    if is_hw_wallet:
        cmd.append("--ledger")
        if mnemonic_index is not None:
            cmd.extend(["--mnemonic-index", str(mnemonic_index)])
    if password:
        cmd.extend(["--unsafe-password", password])
    
    try:
        result = run_cast_command(cmd)
        return result.stdout.strip()
    except CastError as e:
        console.print(f"[red]Error getting wallet address: {str(e)}[/red]")
        raise

def list_wallets() -> List[Tuple[str, str]]:
    """
    List all available cast wallets
    
    Returns:
        List of tuples containing (wallet_name, wallet_address)
    
    Raises:
        CastError: If listing wallets fails
    """
    cmd = ["wallet", "ls"]
    
    try:
        result = run_cast_command(cmd)
        lines = result.stdout.strip().split('\n')
        wallets = []
        
        # Skip the header line
        for line in lines[1:]:
            if line and not line.startswith('NAME'):
                parts = line.split()
                if len(parts) >= 2:  # Ensure we have both name and address
                    wallets.append((parts[0], parts[1]))
        
        return wallets
    except CastError as e:
        console.print(f"[red]Error listing wallets: {str(e)}[/red]")
        raise

def get_wallet_names() -> List[str]:
    """
    Get only the names of available wallets
    
    Returns:
        List of wallet names
    """
    wallets = list_wallets()
    return [name for name, _ in wallets]

def create_wallet(name: str, password: Optional[str] = None, 
                  mnemonic: Optional[str] = None, private_key: Optional[str] = None) -> str:
    """
    Create a new wallet
    
    Args:
        name: Name for the new wallet
        password: Optional password to encrypt the wallet
        mnemonic: Optional mnemonic to use (if not provided, one will be generated)
        private_key: Optional private key to import
        
    Returns:
        The address of the new wallet
    
    Raises:
        CastError: If wallet creation fails
    """
    cmd = ["wallet", "new", name]
    
    if password:
        cmd.extend(["--password", password])
    if mnemonic:
        cmd.extend(["--mnemonic", mnemonic])
    if private_key:
        cmd.extend(["--private-key", private_key])
    
    try:
        result = run_cast_command(cmd)
        # Parse the output to get the address
        for line in result.stdout.strip().split('\n'):
            if line.startswith('Address:'):
                return line.split()[1].strip()
        raise CastError("Failed to parse wallet address from output")
    except CastError as e:
        console.print(f"[red]Error creating wallet: {str(e)}[/red]")
        raise

def import_ledger(name: str, mnemonic_index: int = 0) -> str:
    """
    Import a Ledger hardware wallet
    
    Args:
        name: Name for the imported wallet
        mnemonic_index: Mnemonic index (default: 0)
        
    Returns:
        The address of the imported wallet
    
    Raises:
        CastError: If Ledger import fails
    """
    cmd = ["wallet", "import-ledger", name, "--mnemonic-index", str(mnemonic_index)]
    
    try:
        result = run_cast_command(cmd)
        # Parse the output to get the address
        for line in result.stdout.strip().split('\n'):
            if line.startswith('Address:'):
                return line.split()[1].strip()
        raise CastError("Failed to parse ledger address from output")
    except CastError as e:
        console.print(f"[red]Error importing Ledger: {str(e)}[/red]")
        raise

def select_wallet() -> Tuple[str, str]:
    """
    Interactive prompt for user to select a wallet from available ones
    
    Returns:
        Tuple of (wallet_name, wallet_address)
    
    Raises:
        ValueError: If no wallets are available
        CastError: If listing wallets fails
    """
    try:
        wallets = list_wallets()
        
        if not wallets:
            console.print("[red]No wallets found.[/red]")
            console.print("Create a wallet first with: cast wallet new")
            raise ValueError("No wallets available")
        
        # Display wallets with numbers for selection
        console.print("[bold]Available wallets:[/bold]")
        for i, (wallet, address) in enumerate(wallets):
            console.print(f"  {i+1}. {wallet} - {address}")
        
        # Get user selection
        while True:
            try:
                selection = console.input("\nSelect a wallet: ")
                index = int(selection) - 1
                if 0 <= index < len(wallets):
                    return wallets[index][0] # Return the wallet name only
                else:
                    console.print(f"[red]Invalid selection. Please enter a number between 1 and {len(wallets)}.[/red]")
            except ValueError:
                console.print("[red]Please enter a valid number.[/red]")
    except ValueError as e:
        console.print(f"[red]Error selecting wallet: {str(e)}[/red]")
        raise

# Utility functions

def get_abi(address: str, etherscan_api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Get ABI for a contract
    
    Args:
        address: The contract address
        etherscan_api_key: Optional Etherscan API key
    
    Returns:
        The contract ABI as a list of dictionaries
    
    Raises:
        CastError: If getting ABI fails
    """
    cmd = ["abi", address]
    
    if etherscan_api_key:
        cmd.extend(["--etherscan-api-key", etherscan_api_key])
    
    try:
        result = run_cast_command(cmd)
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise CastError(f"Failed to parse ABI JSON: {result.stdout}")
    except CastError as e:
        console.print(f"[red]Error getting contract ABI: {str(e)}[/red]")
        raise

def call_contract(address: str, function_signature: str, *args, 
                 rpc_url: Optional[str] = None) -> str:
    """
    Call a contract function (read-only)
    
    Args:
        address: Contract address
        function_signature: Function signature (e.g., "balanceOf(address)")
        *args: Function arguments
        rpc_url: Optional RPC URL
        
    Returns:
        The function result as a string
    
    Raises:
        CastError: If the call fails
    """
    cmd = ["call", address, function_signature]
    cmd.extend([str(arg) for arg in args])
    
    if rpc_url:
        cmd.extend(["--rpc-url", rpc_url])
    
    try:
        result = run_cast_command(cmd)
        return result.stdout.strip()
    except CastError as e:
        console.print(f"[red]Error calling contract: {str(e)}[/red]")
        raise

def send_transaction(address: str, function_signature: str, *args, 
                    from_account: Optional[str] = None, 
                    value: Optional[str] = None,
                    gas_limit: Optional[int] = None,
                    rpc_url: Optional[str] = None,
                    password: Optional[str] = None) -> str:
    """
    Send a transaction to a contract
    
    Args:
        address: Contract address
        function_signature: Function signature (e.g., "transfer(address,uint256)")
        *args: Function arguments
        from_account: Account name to use for sending
        value: ETH value to send with transaction (in wei)
        gas_limit: Optional gas limit
        rpc_url: Optional RPC URL
        password: Optional password for the account
        
    Returns:
        The transaction hash
    
    Raises:
        CastError: If the transaction fails
    """
    cmd = ["send", "--json"]
    
    if from_account:
        cmd.extend(["--from", from_account])
    if value:
        cmd.extend(["--value", value])
    if gas_limit:
        cmd.extend(["--gas-limit", str(gas_limit)])
    if rpc_url:
        cmd.extend(["--rpc-url", rpc_url])
    if password:
        cmd.extend(["--password", password])
    
    cmd.extend([address, function_signature])
    cmd.extend([str(arg) for arg in args])
    
    try:
        result = run_cast_command(cmd)
        tx_data = json.loads(result.stdout)
        return tx_data.get("transactionHash")
    except json.JSONDecodeError:
        raise CastError(f"Failed to parse transaction JSON: {result.stdout}")
    except CastError as e:
        console.print(f"[red]Error sending transaction: {str(e)}[/red]")
        raise

def estimate_gas(address: str, function_signature: str, *args, 
                from_account: Optional[str] = None,
                value: Optional[str] = None,
                rpc_url: Optional[str] = None) -> int:
    """
    Estimate gas for a transaction
    
    Args:
        address: Contract address
        function_signature: Function signature
        *args: Function arguments
        from_account: Optional account to use for estimation
        value: Optional ETH value (in wei)
        rpc_url: Optional RPC URL
        
    Returns:
        Estimated gas as an integer
    
    Raises:
        CastError: If gas estimation fails
    """
    cmd = ["estimate"]
    
    if from_account:
        cmd.extend(["--from", from_account])
    if value:
        cmd.extend(["--value", value])
    if rpc_url:
        cmd.extend(["--rpc-url", rpc_url])
    
    cmd.extend([address, function_signature])
    cmd.extend([str(arg) for arg in args])
    
    try:
        result = run_cast_command(cmd)
        return int(result.stdout.strip())
    except ValueError:
        raise CastError(f"Failed to parse gas estimate: {result.stdout}")
    except CastError as e:
        console.print(f"[red]Error estimating gas: {str(e)}[/red]")
        raise

def check_cast_installed() -> bool:
    """
    Check if cast is installed and available
    
    Returns:
        True if cast is installed, False otherwise
    """
    try:
        run_cast_command(["--version"], check=False)
        return True
    except Exception:
        return False
