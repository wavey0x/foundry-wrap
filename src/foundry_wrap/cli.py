"""Command-line interface for foundry-wrap."""

import os
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
import toml

from foundry_wrap.settings import (
    GLOBAL_CONFIG_PATH, 
    create_default_global_config,
    load_settings,
    FoundryWrapSettings
)
from foundry_wrap.interface_manager import InterfaceManager
from foundry_wrap.script_parser import ScriptParser
from foundry_wrap.version import __version__ as VERSION

console = Console()

@click.group()
@click.version_option(VERSION, prog_name="foundry-wrap")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option("--config", "-c", type=click.Path(exists=True), help="Path to config file")
@click.option("--interfaces-path", type=str, help="Local path for storing interfaces")
@click.option("--rpc-url", type=str, help="RPC URL to use for Ethereum interactions")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, config: Optional[str], 
        interfaces_path: Optional[str], rpc_url: Optional[str]) -> None:
    """Foundry Script Wrapper - Dynamic interface generation for Foundry scripts."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    
    # Collect CLI options for config
    cli_options = {}
    if interfaces_path:
        cli_options["interfaces.local_path"] = interfaces_path
    if rpc_url:
        cli_options["rpc.url"] = rpc_url
    
    # Load settings with potential CLI overrides
    settings = load_settings(config, cli_options)
    ctx.obj["settings"] = settings
    # For backward compatibility
    ctx.obj["config"] = settings.model_dump(mode="python")

@cli.command()
@click.argument("script", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Show changes without applying them")
@click.option("--clean", is_flag=True, help="Remove injected interfaces")
@click.pass_context
def run(ctx: click.Context, script: str, dry_run: bool, clean: bool) -> None:
    """Process a Foundry script and handle interface generation."""
    try:
        script_path = Path(script)
        parser = ScriptParser(script_path)
        interface_manager = InterfaceManager(ctx.obj["settings"])

        if clean:
            parser.clean_interfaces()
            return

        # Parse the script and get required interfaces
        interfaces = parser.parse_interfaces()
        
        if dry_run:
            console.print(Panel.fit(
                f"Would process {len(interfaces)} interfaces:\n" +
                "\n".join(f"- {name} for {address}" for name, address in interfaces.items()),
                title="Dry Run"
            ))
            return

        # Process each interface
        for interface_name, address in interfaces.items():
            interface_manager.process_interface(interface_name, address)

        # Update the script with the new interfaces
        parser.update_script(interfaces)

    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
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
@click.option("--safe-proposer", type=str, help="Default Safe proposer address to use")
@click.option("--rpc-url", type=str, help="Default RPC URL to use")
@click.pass_context
def config(ctx: click.Context, global_config: bool, interfaces_path: Optional[str], 
           global_interfaces_path: Optional[str], safe_address: Optional[str],
           safe_proposer: Optional[str], rpc_url: Optional[str]) -> None:
    """Configure foundry-wrap settings."""
    if global_config:
        # Make sure global config exists
        if not GLOBAL_CONFIG_PATH.exists():
            create_default_global_config()
        
        config_path = GLOBAL_CONFIG_PATH
        console.print(f"Editing global config at [blue]{config_path}[/blue]")
    else:
        # Project config
        config_path = Path(".foundry-wrap.toml")
        if not config_path.exists():
            # Create a default project config using a subset of settings
            settings = FoundryWrapSettings()
            config_data = {
                "interfaces": {
                    "local_path": settings.interfaces.local_path,
                }
            }
            with open(config_path, "w") as f:
                toml.dump(config_data, f)
            console.print(f"Created project config at [blue]{config_path}[/blue]")
    
    # Update specific settings if provided
    if any([interfaces_path, global_interfaces_path, safe_address, safe_proposer, rpc_url]):
        # Load existing config
        with open(config_path, "r") as f:
            config_data = toml.load(f)
        
        # Initialize sections if needed
        if "interfaces" not in config_data:
            config_data["interfaces"] = {}
        if "safe" not in config_data:
            config_data["safe"] = {}
        if "rpc" not in config_data:
            config_data["rpc"] = {}
        
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
            
        if safe_proposer:
            config_data["safe"]["safe_proposer"] = safe_proposer
            console.print(f"Set Safe proposer address to [green]{safe_proposer}[/green]")
            
        if rpc_url:
            config_data["rpc"]["url"] = rpc_url
            console.print(f"Set RPC URL to [green]{rpc_url}[/green]")
        
        # Save updated config
        with open(config_path, "w") as f:
            toml.dump(config_data, f)
        
    else:
        # Just show the current config
        console.print(f"Current configuration ([blue]{config_path}[/blue]):")
        with open(config_path, "r") as f:
            console.print(f.read())

@cli.command()
@click.argument("script", type=click.Path(exists=True))
@click.option("--account", help="The account to use for signing")
@click.option("--password", help="Password for the account")
@click.option("--safe-address", help="Safe address to use (overrides config)")
@click.option("--rpc-url", help="RPC URL to use (overrides config)")
@click.option("--dry-run", is_flag=True, help="Generate the transaction but do not submit it")
@click.option("--clean", is_flag=True, help="Remove injected interfaces after execution")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.pass_context
def safe(ctx: click.Context, script: str, account: str, password: str, 
         safe_address: str, rpc_url: str, dry_run: bool, clean: bool, verbose: bool) -> None:
    """Process a script and create/submit a Safe transaction."""
    try:
        # First, process FW-INTERFACE directives in the script
        script_path = Path(script)
        if not script_path.exists():
            console.print(f"[red]Error:[/red] Script not found: {script}")
            sys.exit(1)
            
        # Debug output of script content before processing
        if verbose:
            console.print("Script content before processing:")
            console.print(script_path.read_text())
            
        # Process interfaces
        parser = ScriptParser(script_path)
        interfaces = parser.parse_interfaces()
        
        if verbose:
            console.print(f"Found interfaces: {interfaces}")
        
        # Process each interface if any were found
        if interfaces:
            console.print(f"Processing {len(interfaces)} interfaces in the script...")
            interface_manager = InterfaceManager(ctx.obj["settings"])
            
            for interface_name, address in interfaces.items():
                console.print(f"Processing interface {interface_name} for address {address}...")
                interface_file = interface_manager.process_interface(interface_name, address)
                console.print(f"Interface file created: {interface_file}")
            
            # Update the script with the new interfaces
            parser.update_script(interfaces)
            console.print("[green]Interfaces processed and injected successfully![/green]")
            
            # Debug output of script content after processing
            if verbose:
                console.print("Script content after processing:")
                console.print(script_path.read_text())
        else:
            console.print("[yellow]No interfaces found in the script.[/yellow]")
        
        # Try to import safe functionality 
        from foundry_wrap.safe import foundry_wrap_safe_command
    except ImportError as e:
        # Clean error message for missing dependencies
        console.print(f"[red]Error:[/red] Safe functionality is required but not available.")
        console.print("This application requires safe-eth-py and related dependencies.")
        console.print("\nTo use foundry-wrap with uvx:")
        console.print("    uvx foundry-wrap [command] [options]")
        console.print("\nOr install with safe dependencies:")
        console.print("    pip install 'foundry-wrap[safe]'")
        console.print(f"\nError details: {str(e)}")
        sys.exit(1)

    # Use settings values if available
    settings = ctx.obj["settings"]
    rpc_url = rpc_url or settings.rpc.url
    safe_address = safe_address or settings.safe.safe_address
    
    # Check required parameters
    missing_params = []
    if not rpc_url:
        missing_params.append("RPC URL")
    if not safe_address:
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
        if "Safe proposer address" in missing_params:
            cmd_parts.append("--safe-proposer <YOUR_ADDRESS>")
            
        console.print(f"  [green]{' '.join(cmd_parts)}[/green]")
        
        # If interfaces were injected, clean them up on error
        if interfaces and clean:
            parser.clean_interfaces()
            console.print("[yellow]Interfaces cleaned from script.[/yellow]")
            
        sys.exit(1)
    
    # Execute safe command
    console.print(f"Running forge script with RPC URL: {rpc_url}")
    success, tx_hash, error = foundry_wrap_safe_command(
        script_path=str(script_path),
        project_dir=None,  # Use current directory
        account=account,
        password=password,
        rpc_url=rpc_url,
        safe_address=safe_address,
        dry_run=dry_run
    )
    
    # If requested, clean up the injected interfaces
    if interfaces and clean:
        parser.clean_interfaces()
        console.print("[yellow]Interfaces cleaned from script.[/yellow]")
    
    if success:
        console.print(f"[green]Safe transaction created successfully![/green]")
        console.print(f"Transaction hash: [bold]{tx_hash}[/bold]")
        if dry_run:
            console.print("[yellow]Dry run - transaction not submitted[/yellow]")
    else:
        console.print(f"[red]Error:[/red] {error}")
        
        # If interfaces were injected but command failed, clean them up
        if interfaces and clean:
            parser.clean_interfaces()
            console.print("[yellow]Interfaces cleaned from script.[/yellow]")
            
        sys.exit(1)

def main():
    """Entry point for the CLI."""
    # Ensure global config exists
    if not GLOBAL_CONFIG_PATH.exists():
        create_default_global_config()
        
    cli(obj={}) 