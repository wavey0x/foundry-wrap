"""Command-line interface"""

import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any

import click
from rich.console import Console
from rich.panel import Panel
import toml

from foundry_wrap.settings import (
    GLOBAL_CONFIG_PATH, 
    create_default_config,
    load_settings,
    FoundryWrapSettings,
    RpcSettings,
    SafeSettings
)
from foundry_wrap.interface_manager import InterfaceManager
from foundry_wrap.script_parser import ScriptParser
from foundry_wrap.version import __version__ as VERSION
from foundry_wrap.safe import run_command

console = Console()

@click.group()
@click.version_option(VERSION, prog_name="foundry-wrap")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Foundry Script Wrapper - Dynamic interface generation for Foundry scripts."""
    ctx.ensure_object(dict)

@cli.command()
@click.argument("script", type=click.Path(exists=True), required=True)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option("--config", "-c", type=click.Path(exists=True), help="Path to config file")
@click.option("--interfaces-path", type=str, help="Local path for storing interfaces")
@click.option("--rpc-url", type=str, help="RPC URL to use for Ethereum interactions")
@click.option("--proposer", help="The proposer to use for signing")
@click.option("--password", help="Password for the account")
@click.option("--safe-address", help="Safe address to use (overrides config)")
@click.option("--dry-run", is_flag=True, help="Generate the transaction but do not submit it")
@click.option("--clean", is_flag=True, help="Remove injected interfaces after execution")
@click.pass_context
def run(ctx: click.Context, script: str, verbose: bool, config: Optional[str], 
        interfaces_path: Optional[str], rpc_url: Optional[str], proposer: Optional[str],
        password: Optional[str], safe_address: Optional[str], dry_run: bool, clean: bool) -> None:
    """Run a Foundry script and create/submit a Safe transaction."""
    ctx.obj["verbose"] = verbose
    
    # Prepare CLI options for loading settings
    cli_options = {
        "interfaces.local_path": interfaces_path,
        "rpc.url": rpc_url,
        "safe.proposer": proposer,
        "safe.safe_address": safe_address
    }

    # Load settings with proper precedence
    settings = load_settings(config_path=config, cli_options=cli_options)

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
        
        cmd_parts = ["foundry-wrap config --global"]
        if "RPC URL" in missing_params:
            cmd_parts.append("--rpc-url <YOUR_RPC_URL>")
        if "Safe address" in missing_params:
            cmd_parts.append("--safe-address <YOUR_SAFE_ADDRESS>")
            
        console.print(f"  [green]{' '.join(cmd_parts)}[/green]")
        
        # If interfaces were injected, clean them up on error
        if clean:
            parser = ScriptParser(Path(script), verbose=verbose)
            parser.clean_interfaces()
            console.print("[yellow]Interfaces cleaned from script.[/yellow]")
            
        sys.exit(1)
    
    console.print(f"[blue]Safe address:[/blue] {settings.safe.safe_address if settings.safe.safe_address else 'Not set'}")
    if settings.safe.proposer:
        console.print(f"[blue]Proposer:[/blue] {settings.safe.proposer}")
    console.print(f"[blue]RPC URL:[/blue] {settings.rpc.url if settings.rpc.url else 'Not set'}\n")
    
    success, tx_hash, error = run_command(
        script_path=str(script),
        project_dir=None,  # Use current directory
        proposer=settings.safe.proposer,
        password=password,
        rpc_url=settings.rpc.url,
        safe_address=settings.safe.safe_address,
        dry_run=dry_run
    )
    
    # If requested, clean up the injected interfaces
    if clean:
        parser = ScriptParser(Path(script), verbose=verbose)
        parser.clean_interfaces()
        console.print("[yellow]Interfaces cleaned from script.[/yellow]")
    
    if success:
        console.print(f"[green]Safe transaction created successfully![/green]")
        console.print(f"View it here: https://app.safe.global/transactions/queue?safe={settings.safe.safe_address}")
        if dry_run:
            console.print("[yellow]Dry run - transaction not submitted[/yellow]")
    else:
        console.print(f"[red]Error:[/red] {error}")
        
        # If interfaces were injected but command failed, clean them up
        if clean:
            parser.clean_interfaces()
            console.print("[yellow]Interfaces cleaned from script.[/yellow]")
            
        sys.exit(1)

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
           proposer: Optional[str], rpc_url: Optional[str], cache_path: Optional[str],
           cache_enabled: Optional[bool], etherscan_api_key: Optional[str]) -> None:
    """Configure settings."""
    if global_config:
        # Make sure global config exists
        if not GLOBAL_CONFIG_PATH.exists():
            create_default_config(GLOBAL_CONFIG_PATH, is_global=True)
        
        config_path = GLOBAL_CONFIG_PATH
        console.print(f"Editing global config at [blue]{config_path}[/blue]")
    else:
        # Project config
        config_path = Path("foundry-wrap.toml")
        if not config_path.exists():
            create_default_config(config_path, is_global=False)
            console.print(f"Created project config at [blue]{config_path}[/blue]")
    
    # Update specific settings if provided
    if any([interfaces_path, global_interfaces_path, safe_address, 
            proposer, rpc_url, cache_path, cache_enabled, etherscan_api_key]):
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
        
    else:
        # Just show the current config
        console.print(f"Current configuration ([blue]{config_path}[/blue]):")
        with open(config_path, "r") as f:
            console.print(f.read())

def main():
    """Entry point for the CLI."""
    # Ensure global config exists
    if not GLOBAL_CONFIG_PATH.exists():
        create_default_global_config()
        
    cli(obj={})  # This is all you need for a Click application 