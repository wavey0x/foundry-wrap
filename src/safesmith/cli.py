"""Command-line interface"""

import sys
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.panel import Panel
import toml


from safesmith.interface_manager import InterfaceManager
from safesmith.script_parser import ScriptParser
from safesmith.version import __version__ as VERSION
from safesmith.cast import select_wallet, get_address, WalletError
from safesmith.errors import SafeError, NetworkError, ScriptError
from safesmith.safe import (
    run_command, 
    fetch_next_nonce,
    delete_safe_transaction,
    fetch_safe_transaction_by_nonce
)
from safesmith.settings import (
    GLOBAL_CONFIG_PATH, 
    create_default_config,
    load_settings,
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
@click.option("--proposer", help="The proposer address to use for signing")
@click.option("--proposer-alias", help="The proposer alias as it appears in cast wallet")
@click.option("--password", help="Password for the account (not recommended to pass in cleartext)")
@click.option("--post", is_flag=True, help="Post the transaction to the service")
@click.option("--clean", is_flag=True, help="Remove injected interfaces after execution")
@click.option("--skip-broadcast-check", is_flag=True, help="Skip checking for vm.startBroadcast in the script")
@click.option("--skip-interfaces", is_flag=True, help="Skip interface parsing and processing")
@click.pass_context
def run(ctx: click.Context, script: str, verbose: bool, rpc_url: Optional[str], safe_address: Optional[str], 
        nonce: Optional[int], proposer: Optional[str], proposer_alias: Optional[str], password: Optional[str], 
        post: bool, clean: bool, skip_broadcast_check: bool, skip_interfaces: bool) -> None:
    """Run a Foundry script and create/submit a Safe transaction."""

    ctx.obj["verbose"] = verbose
    
    # Prepare CLI options for loading settings
    cli_options = {
        "rpc.url": rpc_url,
        "safe.proposer": proposer,
        "safe.proposer_alias": proposer_alias,
        "safe.safe_address": safe_address,
        "safe.skip_broadcast_check": skip_broadcast_check
    }

    parser = ScriptParser(Path(script), verbose=verbose)

    # Load settings with proper precedence
    settings = load_settings(cli_options=cli_options)
    parser.check_broadcast_block(post, settings.safe.skip_broadcast_check)


    # Set the settings in context
    ctx.obj["settings"] = settings
    
    # Parse and process interfaces if not skipped
    if not skip_interfaces:
        # Initialize the interface manager first to make presets available
        interface_manager = InterfaceManager(settings)
        
        # Pass the interface manager to the script parser to enable preset detection
        parser = ScriptParser(Path(script), verbose=verbose, interface_manager=interface_manager)
        
        # Parse interfaces (both address-based and preset-based)
        interfaces = parser.parse_interfaces()
        
        if interfaces:
            # Create a display-friendly representation of interfaces
            display_items = []
            for name, address in interfaces.items():
                if address is None:
                    display_items.append(f"- @{name} (preset)")
                else:
                    display_items.append(f"- @{name} at address {address}")
            
            # Display found interfaces panel
            interface_panel = Panel.fit(
                "\n".join(display_items),
                title=f"Found {len(interfaces)} interfaces"
            )
            console.print(interface_panel)
            
            # Process each interface
            processed_paths = {}
            
            with console.status("[bold green]Processing interfaces..."):
                for name, address in interfaces.items():
                    try:
                        path = interface_manager.process_interface(name, address)
                        processed_paths[name] = path
                        console.print(f"[green]✓[/green] Processed interface [bold]{name}[/bold]")
                    except Exception as e:
                        console.print(f"[red]✗[/red] Failed to process interface [bold]{name}[/bold]: {str(e)}")
            
            # Update the script with imports
            parser.update_script(interfaces)
            console.print(f"[green]Script updated with interface imports and references.[/green]")

    
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

        print()
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
        
        # Disable traceback display to prevent stack traces
        old_tracebacklimit = getattr(sys, 'tracebacklimit', None)
        sys.tracebacklimit = 0
        
        try:
            run_command(
                script_path=str(script),
                project_dir=None,  # Use current directory
                proposer=settings.safe.proposer,
                proposer_alias=settings.safe.proposer_alias,
                password=password,
                rpc_url=settings.rpc.url,
                safe_address=settings.safe.safe_address,
                post=post,
                nonce=nonce,
                skip_broadcast_check=settings.safe.skip_broadcast_check
            )
        except (SafeError, NetworkError, WalletError, ScriptError) as e:
            # Single, clean error message at CLI level
            console.print(f"[red]Error:[/red] {str(e)}")
            
            # Add extra helpful hint for broadcast block errors
            if "Could not find last run data" in str(e):
                console.print("\n[yellow]Hint:[/yellow] Make sure your script includes vm.startBroadcast() and vm.stopBroadcast()")
                console.print("      Or use --skip-broadcast-check to bypass this check.")
                
            # If requested, clean up the injected interfaces on error
            if clean:
                parser.clean_interfaces()
                console.print("[yellow]Interfaces cleaned from script.[/yellow]")
            sys.exit(1)
        except Exception as e:
            # Catch-all for any other exceptions
            console.print(f"[red]Unexpected error:[/red] {str(e)}")
            if clean:
                parser.clean_interfaces()
                console.print("[yellow]Interfaces cleaned from script.[/yellow]")
            sys.exit(1)
        finally:
            # Restore traceback settings
            if old_tracebacklimit is not None:
                sys.tracebacklimit = old_tracebacklimit
        
    except Exception as e:
        # Handle other types of errors (like nonce fetching, etc)
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
@click.option("--skip-broadcast-check", type=bool, help="Skip checking for vm.startBroadcast in scripts")
@click.pass_context
def config(ctx: click.Context, global_config: bool, interfaces_path: Optional[str], 
           global_interfaces_path: Optional[str], safe_address: Optional[str],
           proposer: Optional[str], rpc_url: Optional[str], 
           cache_path: Optional[str], cache_enabled: Optional[bool], 
           etherscan_api_key: Optional[str], skip_broadcast_check: Optional[bool]) -> None:
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
            proposer, rpc_url, cache_path, cache_enabled, etherscan_api_key, skip_broadcast_check]):
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
            
            if skip_broadcast_check is not None:
                config_data["safe"]["skip_broadcast_check"] = skip_broadcast_check
                console.print(f"Set skip_broadcast_check to [green]{skip_broadcast_check}[/green]")
            
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

@cli.command(name="process-interfaces")
@click.argument("script", type=click.Path(exists=True), required=True)
@click.option("--verbose", is_flag=True, help="Enable verbose output")
@click.option("--clean", is_flag=True, help="Remove injected interfaces after processing")
@click.pass_context
def process_interfaces(ctx: click.Context, script: str, verbose: bool, clean: bool) -> None:
    """
    Parse script for @interface directives, download interfaces, and update the script.
    
    This command will:
    1. Scan the script for @InterfaceName directives
    2. Download the interfaces from Etherscan if needed
    3. Update the script with proper imports
    """
    # Initialize parser
    parser = ScriptParser(Path(script), verbose=verbose)
    
    # Parse interfaces from the script
    try:
        interfaces = parser.parse_interfaces()
        if not interfaces:
            console.print("[yellow]No interfaces found in script.[/yellow]")
            return
        
        # Print found interfaces
        interface_panel = Panel.fit(
            "\n".join([f"- @{name} at address {addr}" for name, addr in interfaces.items()]),
            title=f"Found {len(interfaces)} interfaces"
        )
        console.print(interface_panel)
        
        # Process each interface
        interface_manager = InterfaceManager(ctx.obj["settings"])
        processed_paths = {}
        
        with console.status("[bold green]Processing interfaces..."):
            for name, address in interfaces.items():
                try:
                    path = interface_manager.process_interface(name, address)
                    processed_paths[name] = path
                    console.print(f"[green]✓[/green] Processed interface [bold]{name}[/bold]")
                except Exception as e:
                    console.print(f"[red]✗[/red] Failed to process interface [bold]{name}[/bold]: {str(e)}")
        
        # Update the script with imports
        parser.update_script(interfaces)
        console.print(f"[green]Script updated with interface imports and references.[/green]")
        
        # Clean up if requested
        if clean:
            parser.clean_interfaces()
            console.print("[yellow]Interfaces cleaned from script.[/yellow]")
            
    except Exception as e:
        console.print(f"[red]Error processing interfaces: {str(e)}[/red]")
        sys.exit(1)

@cli.command(name="sync-presets")
@click.option("--verbose", is_flag=True, help="Enable verbose output")
@click.pass_context
def sync_presets(ctx: click.Context, verbose: bool) -> None:
    """
    Synchronize interface presets with the latest from the user presets directory.
    
    This command will:
    1. Scan the presets directory for interface files
    2. Build an index for quick lookup
    3. Make presets available for use in scripts with @ directives
    """
    try:
        # Load settings
        settings = load_settings()
        
        # Initialize interface manager
        interface_manager = InterfaceManager(settings)
        
        # Update presets index
        with console.status("[bold green]Synchronizing presets..."):
            interface_manager.update_preset_index()
        
        # Get all available presets
        presets = interface_manager.load_preset_index()
        
        # Show summary
        if verbose:
            console.print("[green]Available presets:[/green]")
            for name, path in sorted(presets.items()):
                console.print(f"  - {name}: {path}")
        else:
            console.print(f"[green]Synchronized {len(presets)} interface presets[/green]")
        
    except Exception as e:
        console.print(f"[red]Error synchronizing presets: {str(e)}[/red]")
        sys.exit(1)

@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize a new safesmith project."""
    # Check if already initialized
    config_path = Path("safesmith.toml")
    if config_path.exists():
        console.print("[red]Error:[/red] Project is already initialized with safesmith.toml")
        sys.exit(1)
    
    # Check if in a foundry project
    foundry_dirs = ["script", "src"]
    missing_dirs = [d for d in foundry_dirs if not Path(d).exists()]
    
    if missing_dirs:
        console.print(f"[yellow]Warning:[/yellow] This doesn't appear to the root level of a Foundry project")
        if not click.confirm("Do you want to initialize anyway?", default=False):
            console.print("[yellow]Initialization cancelled.[/yellow]")
            sys.exit(0)
    
    # Create default config
    create_default_config(config_path, is_global=False)
    console.print(f"[green]Created project config at {config_path}[/green]")
    
    # Ensure safesmith.toml is in .gitignore
    gitignore_path = Path(".gitignore")
    if gitignore_path.exists():
        with open(gitignore_path, "r") as f:
            gitignore_content = f.read()
        if "safesmith.toml" not in gitignore_content:
            with open(gitignore_path, "a") as f:
                f.write("\nsafesmith.toml\n")
            console.print("[green]Added safesmith.toml to .gitignore[/green]")
    else:
        with open(gitignore_path, "w") as f:
            f.write("safesmith.toml\n")
        console.print("[green]Created .gitignore with safesmith.toml[/green]")
    
    console.print("\n[bold]Next steps:[/bold]")
    console.print("1. Edit safesmith.toml with your project's Safe information")
    console.print("2. Run `ss run script/YourScript.s.sol` to execute a script")

def main():
    """Entry point for the CLI."""
    # Ensure global config exists
    if not GLOBAL_CONFIG_PATH.exists():
        create_default_config(GLOBAL_CONFIG_PATH, is_global=True)
    
    # Globally disable traceback printing to avoid stack traces
    old_tracebacklimit = getattr(sys, 'tracebacklimit', None)
    sys.tracebacklimit = 0
    
    try:
        cli(obj={})
    except Exception as e:
        # Last resort error handler
        console.print(f"[red]Error:[/red] {str(e)}")
        sys.exit(1)
    finally:
        # Restore original traceback setting
        if old_tracebacklimit is not None:
            sys.tracebacklimit = old_tracebacklimit 