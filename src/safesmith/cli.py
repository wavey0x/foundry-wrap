"""Command-line interface"""

import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import click
from rich.console import Console
from rich.panel import Panel
import toml
from getpass import getpass
import json
import requests

from safesmith.settings import (
    GLOBAL_CONFIG_PATH, 
    create_default_config,
    load_settings,
)
from safesmith.interface_manager import InterfaceManager
from safesmith.script_parser import ScriptParser
from safesmith.version import __version__ as VERSION
from safesmith.cast import select_wallet, get_address, WalletError
from safesmith.errors import SafeError, NetworkError, ConfigError, result_or_raise, handle_errors
from safesmith.safe import (
    run_command, 
    fetch_next_nonce,
    delete_safe_transaction,
    fetch_safe_transaction_by_nonce
)

console = Console()

@click.group()
@click.version_option(VERSION, prog_name="safesmith")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Foundry Script Wrapper - Dynamic interface generation for Foundry scripts."""
    ctx.ensure_object(dict)
    try:
        ctx.obj['settings'] = load_settings()
    except Exception as e:
        console.print(f"[red]Error loading settings: {str(e)}[/red]")
        ctx.obj['settings'] = None

@cli.command()
@click.argument("script", type=click.Path(exists=True), required=True)
@click.option("--verbose", is_flag=True, help="Enable verbose output")
@click.option("--rpc-url", type=str, help="RPC URL to use for Ethereum interactions")
@click.option("--safe-address", help="Safe address to use (overrides config)")
@click.option("--nonce", type=int, help="Custom nonce. If not provided, the next nonce will be used.")
@click.option("--proposer", help="The proposer to use for signing")
@click.option("--proposer-alias", help="The proposer alias as it appears in cast wallet")
@click.option("--password", help="Password for the account (not recommended to pass in cleartext)")
@click.option("--post", is_flag=True, help="Post the transaction to the service")
@click.option("--clean", is_flag=True, help="Remove injected interfaces after execution")
@click.pass_context
def run(ctx: click.Context, script: str, verbose: bool, rpc_url: Optional[str], safe_address: Optional[str], nonce: Optional[int], proposer: Optional[str], proposer_alias: Optional[str], password: Optional[str], post: bool, clean: bool) -> None:
    """Run a Foundry script and create/submit a Safe transaction."""

    ctx.obj["verbose"] = verbose
    
    # Prepare CLI options for loading settings
    cli_options = {
        "rpc.url": rpc_url,
        "safe.proposer": proposer,
        "safe.proposer_alias": proposer_alias,
        "safe.safe_address": safe_address
    }

    parser = ScriptParser(Path(script), verbose=verbose)
    try:
        parser.check_broadcast_block(post)
    except ValueError as e:
        console.print(f"[red]{str(e)}[/red]")
        sys.exit(1)

    # Load settings with proper precedence
    settings = load_settings(cli_options=cli_options)

    ctx.obj["settings"] = settings
    
    # Check required parameters
    missing_params = []
    if not settings.rpc.url:
        missing_params.append("RPC URL")
    if not settings.safe.safe_address:
        missing_params.append("Safe address")
        
    if missing_params:
        console.print(f"[red]Error:[/red] Missing required parameters: {', '.join(missing_params)}")
        console.print(f"You can set these values in your global config at [blue]{GLOBAL_CONFIG_PATH}[/blue]")
        console.print("Run the following command to configure:")
        
        cmd_parts = ["safesmith config --global"]
        if "RPC URL" in missing_params:
            cmd_parts.append("--rpc-url <YOUR_RPC_URL>")
        if "Safe address" in missing_params:
            cmd_parts.append("--safe-address <YOUR_SAFE_ADDRESS>")
            
        console.print(f"  [green]{' '.join(cmd_parts)}[/green]")
        
        # If interfaces were injected, clean them up on error
        if clean:
            parser.clean_interfaces()
            console.print("[yellow]Interfaces cleaned from script.[/yellow]")
            
        sys.exit(1)

    try:
        # Fetch next nonce if not provided
        if nonce is None:
            nonce = fetch_next_nonce(
                settings.safe.safe_address, 
                settings.safe.chain_id
            )
            
        safe_address = settings.safe.safe_address
        proposer = settings.safe.proposer if settings.safe.proposer else 'Not set'
        rpc_url = settings.rpc.url
        chain_id = settings.safe.chain_id

        run_info_panel = Panel.fit(
            f"[light_sky_blue1]Safe address:[/light_sky_blue1] {safe_address}\n"
            f"[light_sky_blue1]Proposer:[/light_sky_blue1] {proposer}\n"
            f"[light_sky_blue1]Nonce:[/light_sky_blue1] {nonce}\n"
            f"[light_sky_blue1]RPC URL:[/light_sky_blue1] {rpc_url}\n"
            f"[light_sky_blue1]Chain ID:[/light_sky_blue1] {chain_id}",
            title="Safe Run Info"
        )

        # Print the panel
        console.print(run_info_panel)
        
        # Run the Safe transaction command
        tx_hash, tx_json = run_command(
            script_path=str(script),
            project_dir=None,  # Use current directory
            proposer=settings.safe.proposer,
            proposer_alias=settings.safe.proposer_alias,
            password=password,
            rpc_url=settings.rpc.url,
            safe_address=settings.safe.safe_address,
            post=post,
            nonce=nonce
        )
        
    except (SafeError, NetworkError, WalletError) as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        # If requested, clean up the injected interfaces on error
        if clean:
            parser.clean_interfaces()
            console.print("[yellow]Interfaces cleaned from script.[/yellow]")
        sys.exit(1)
        
    # If requested, clean up the injected interfaces
    if clean:
        parser.clean_interfaces()
        console.print("[yellow]Interfaces cleaned from script.[/yellow]")

@cli.command()
@click.pass_context
def list(ctx: click.Context) -> None:
    """List all cached interfaces."""
    interface_manager = InterfaceManager(ctx.obj["settings"])
    cached = interface_manager.list_cached_interfaces()
    
    if not cached:
        console.print("[yellow]No cached interfaces found.[/yellow]")
        return
    
    console.print(Panel.fit(
        "Cached Interfaces:\n" +
        "\n".join(f"- {name} ({path})" for name, path in cached.items()),
        title="Interface Cache"
    ))

@cli.command(name="clear-cache")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def clear_cache(ctx: click.Context, confirm: bool) -> None:
    """Clear the global interface cache."""
    interface_manager = InterfaceManager(ctx.obj["settings"])
    
    # Show what will be deleted
    cached = interface_manager.list_cached_interfaces()
    count = len(cached)
    
    if count == 0:
        console.print("[yellow]No cached interfaces found. Nothing to clear.[/yellow]")
        return
    
    # Display warning and confirmation
    console.print(f"[yellow]Warning:[/yellow] This will delete {count} cached interfaces from the global cache")
    console.print(f"Cache location: [blue]{interface_manager.cache_path}[/blue]")
    
    # Ask for confirmation unless --confirm flag is used
    if not confirm:
        confirm = console.input("Are you sure you want to continue? [y/N] ")
        if not confirm.lower().startswith("y"):
            console.print("[yellow]Operation cancelled.[/yellow]")
            return
    
    # Clear the cache
    interface_manager.clear_cache()
    console.print("[green]Global cache cleared successfully.[/green]")

@cli.command()
@click.option("--global", "global_config", is_flag=True, help="Edit global config")
@click.option("--interfaces-path", type=str, help="Default local path for interfaces")
@click.option("--global-interfaces-path", type=str, help="Path for global interfaces")
@click.option("--safe-address", type=str, help="Default Safe address to use")
@click.option("--proposer", type=str, help="Default proposer address to use")
@click.option("--rpc-url", type=str, help="Default RPC URL to use")
@click.option("--cache-path", type=str, help="Path for interface cache")
@click.option("--cache-enabled", type=bool, help="Enable or disable interface caching")
@click.option("--etherscan-api-key", type=str, help="Etherscan API key")
@click.pass_context
def config(ctx: click.Context, global_config: bool, interfaces_path: Optional[str], 
           global_interfaces_path: Optional[str], safe_address: Optional[str],
           proposer: Optional[str], rpc_url: Optional[str], 
           cache_path: Optional[str], cache_enabled: Optional[bool], 
           etherscan_api_key: Optional[str]) -> None:
    """Configure settings."""
    if global_config:
        # Make sure global config exists
        if not GLOBAL_CONFIG_PATH.exists():
            create_default_config(GLOBAL_CONFIG_PATH, is_global=True)
        
        config_path = GLOBAL_CONFIG_PATH
        console.print(f"Editing global config at [blue]{config_path}[/blue]")
    else:
        # Project config
        config_path = Path("safesmith.toml")
        if not config_path.exists():
            create_default_config(config_path, is_global=False)
            console.print(f"Created project config at [blue]{config_path}[/blue]")
    
    # Update specific settings if provided
    if any([interfaces_path, global_interfaces_path, safe_address, 
            proposer, rpc_url, cache_path, cache_enabled, etherscan_api_key]):
        try:
            # Load existing config
            with open(config_path, "r") as f:
                config_data = toml.load(f)
            
            # Initialize sections if needed
            for section in ["interfaces", "safe", "rpc", "cache", "etherscan"]:
                if section not in config_data:
                    config_data[section] = {}
            
            # Update settings
            if interfaces_path:
                config_data["interfaces"]["local_path"] = interfaces_path
                console.print(f"Set local interfaces path to [green]{interfaces_path}[/green]")
                
            if global_interfaces_path:
                config_data["interfaces"]["global_path"] = global_interfaces_path
                console.print(f"Set global interfaces path to [green]{global_interfaces_path}[/green]")
                
            if safe_address:
                config_data["safe"]["safe_address"] = safe_address
                console.print(f"Set Safe address to [green]{safe_address}[/green]")
                
            if proposer:
                config_data["safe"]["proposer"] = proposer
                console.print(f"Set proposer to [green]{proposer}[/green]")
                
            if rpc_url:
                config_data["rpc"]["url"] = rpc_url
                console.print(f"Set RPC URL to [green]{rpc_url}[/green]")
                
            if cache_path:
                config_data["cache"]["path"] = cache_path
                console.print(f"Set cache path to [green]{cache_path}[/green]")
                
            if cache_enabled is not None:
                config_data["cache"]["enabled"] = cache_enabled
                console.print(f"Set cache enabled to [green]{cache_enabled}[/green]")
                
            if etherscan_api_key:
                config_data["etherscan"]["api_key"] = etherscan_api_key
                console.print(f"Set Etherscan API key")
            
            # Save updated config
            with open(config_path, "w") as f:
                toml.dump(config_data, f)
        except Exception as e:
            console.print(f"[red]Error updating config: {str(e)}[/red]")
            sys.exit(1)
        
    else:
        # Just show the current config
        try:
            console.print(f"Current configuration ([blue]{config_path}[/blue]):")
            with open(config_path, "r") as f:
                console.print(f.read())
        except Exception as e:
            console.print(f"[red]Error reading config: {str(e)}[/red]")
            sys.exit(1)

@cli.command(name="delete")
@click.argument("nonce", type=int, required=True)
@click.option("--chain-id", type=int, default=1, help="Ethereum chain ID")
@click.option("--safe-address", type=str, default=None, help="Safe address")
@click.option("--proposer", help="The proposer to use for signing")
@click.option("--proposer-alias", help="The proposer alias as it appears in cast wallet")
@click.option("--password", help="Password for the account (not recommended to pass in cleartext)")
@click.option("--verbose", is_flag=True, help="Enable verbose output")
@click.pass_context
def delete(ctx: click.Context, nonce: int, chain_id: Optional[int], safe_address: Optional[str], 
           proposer: Optional[str], proposer_alias: Optional[str], password: Optional[str], 
           verbose: bool) -> None:
    """Delete a pending Safe transaction by nonce."""
    # Load settings
    cli_options = {
        "safe.proposer": proposer,
        "safe.proposer_alias": proposer_alias,
        "safe.safe_address": safe_address
    }
    settings = load_settings(cli_options=cli_options)

    # Use provided values or fall back to settings
    safe_address = safe_address or settings.safe.safe_address
    proposer = proposer or settings.safe.proposer
    proposer_alias = proposer_alias or settings.safe.proposer_alias
    chain_id = chain_id or getattr(settings.safe, 'chain_id', None) or 1  # Default to Ethereum mainnet
    
    # Check for required parameters
    if not safe_address:
        console.print("[red]Error: Safe address is required. Set it with --safe-address or in your config.[/red]")
        return
    
    safe_tx_hash = fetch_safe_transaction_by_nonce(safe_address, nonce, chain_id)

    if not safe_tx_hash:
        console.print(f"[red]Error: No pending transaction found with nonce {nonce}[/red]")
        sys.exit(1)

    # If no proposer specified, prompt for wallet selection
    if not proposer and not proposer_alias:
        console.print(f"\n[yellow]Please select a proposer wallet...[/yellow]")
        proposer_alias = select_wallet()
        console.print(f"Selected {proposer_alias}")
        proposer = get_address(account=proposer_alias, password=password)
    elif proposer and not proposer_alias:
        console.print(f"\n[yellow]Please select the wallet alias for your set proposer: {proposer}...[/yellow]")
        proposer_alias = select_wallet()
    elif not proposer and proposer_alias:
        proposer = get_address(account=proposer_alias, password=password)
    
    # Display information about the operation
    delete_info_panel = Panel.fit(
        f"[light_sky_blue1]Safe address:[/light_sky_blue1] {safe_address}\n"
        f"[light_sky_blue1]Chain ID:[/light_sky_blue1] {chain_id}\n"
        f"[light_sky_blue1]Target nonce:[/light_sky_blue1] {nonce}\n"
        f"[light_sky_blue1]Proposer:[/light_sky_blue1] {proposer}\n"
        f"[light_sky_blue1]Safe transaction hash:[/light_sky_blue1] {safe_tx_hash}",
        title="Delete Safe Transaction"
    )
    console.print(delete_info_panel)
    
    # Confirm the operation
    if not click.confirm("Are you sure you want to delete this transaction?"):
        console.print("[yellow]Operation aborted by user.[/yellow]")
        return
    
    # Use the delete_safe_transaction function from safe.py - with no error display
    delete_safe_transaction(
        safe_tx_hash=safe_tx_hash,
        safe_address=safe_address,
        nonce=nonce,
        account=proposer_alias,
        password=password,
        chain_id=chain_id,
    )
    
    console.print(f"[green]Successfully deleted Safe transaction with nonce {nonce}[/green]")

def main():
    """Entry point for the CLI."""
    # Ensure global config exists
    if not GLOBAL_CONFIG_PATH.exists():
        create_default_config(GLOBAL_CONFIG_PATH, is_global=True)
        
    cli(obj={})  # This is all you need for a Click application 