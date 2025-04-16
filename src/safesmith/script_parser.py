"""Script parsing and modification"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Match, Union, Set, Tuple

import click
from rich.console import Console
from safesmith.interface_manager import InterfaceManager
from safesmith.settings import SafesmithSettings
from safesmith.errors import ScriptError, handle_errors

# Create console instance
console = Console()

class ScriptParser:
    """Parser for Foundry scripts that handles interface directives."""
    
    # Regex pattern for @ directive detection, ensuring it's not part of a comment
    INTERFACE_PATTERN = r'@([A-Z]\w+)'
    
    def __init__(self, script_path: Path, verbose: bool = False, interface_manager: Optional[InterfaceManager] = None):
        """Initialize the parser with a script path."""
        self.script_path = script_path
        self.verbose = verbose
        self.interface_manager = interface_manager
        if not self.script_path.exists():
            raise ScriptError(f"Script not found: {script_path}")
        self.original_content = self.script_path.read_text()
        self.processed_interfaces = {}
    
    @handle_errors(error_type=ScriptError)
    def parse_interfaces(self) -> Dict[str, Optional[str]]:
        """
        Parse the script and extract interface directives.
        
        Returns:
            Dict mapping interface names to their addresses (None for presets without addresses)
        """
        content = self.script_path.read_text()
        
        # Find all potential interface directives
        interfaces: Dict[str, Optional[str]] = {}
        presets: Set[str] = set()
        
        # Load available presets if interface_manager is provided
        if self.interface_manager:
            presets = set(self.interface_manager.load_preset_index().keys())
        
        lines = content.split('\n')
        
        # Track contract state
        in_contract = False
        current_contract = None
        
        # First pass: find all @Interface(0xAddress) directives
        for i, line in enumerate(lines):
            line = line.strip()
            
            # Skip empty lines or comments
            if not line or line.startswith('//') or line.startswith('/*') or line.startswith('*'):
                continue
            
            # Check for contract declaration
            if line.startswith('contract') or line.startswith('interface'):
                in_contract = True
                current_contract = line.split()[1]  # Get contract name
                continue
                
            # Check for contract end
            if line.startswith('}'):
                if in_contract:
                    in_contract = False
                    current_contract = None
                continue
            
            # Process directives
            # Look for @ directives
            matches = re.finditer(self.INTERFACE_PATTERN, line)
            for match in matches:
                interface_name = match.group(1)
                
                # Check if already processed (to avoid duplicates)
                if interface_name in interfaces:
                    continue
                
                # Try to find an address on the same line
                address_match = re.search(r'0x[a-fA-F0-9]{40}', line)
                if address_match:
                    # This is a regular directive with an address
                    address = address_match.group(0)
                    interfaces[interface_name] = address
                elif interface_name in presets:
                    # This is a preset directive without an address
                    interfaces[interface_name] = None
                    if self.verbose:
                        click.echo(f"Found preset directive: @{interface_name}")
                # Note: directives without addresses that aren't presets are ignored
        
        if self.verbose and interfaces:
            address_count = sum(1 for addr in interfaces.values() if addr is not None)
            preset_count = len(interfaces) - address_count
            click.echo(f"Found {address_count} address-based interfaces and {preset_count} preset interfaces in script")
        
        return interfaces
    
    def _find_import_position(self, lines: List[str]) -> int:
        """Find the appropriate position to insert imports."""
        # First, find the end of the pragma section
        pragma_end = 0
        for i, line in enumerate(lines):
            if line.strip().startswith('pragma'):
                pragma_end = i + 1
                break
        
        # Then find the first non-import, non-empty line after pragma
        for i in range(pragma_end, len(lines)):
            line = lines[i].strip()
            if not line or line.startswith('import'):
                continue
            return i
        
        return len(lines)
    
    def _find_contract_start(self, lines: List[str]) -> int:
        """Find the line where the contract definition starts."""
        for i, line in enumerate(lines):
            if 'contract' in line or 'interface' in line or 'library' in line:
                return i
        return len(lines)
    
    @handle_errors(error_type=ScriptError)
    def update_script(self, interfaces: Dict[str, Optional[str]]) -> None:
        """Update the script with the actual interface names."""
        if self.verbose:
            click.echo("Updating script with interface imports")
        content = self.script_path.read_text()
        
        # Split content into lines
        lines = content.split('\n')
        
        # Track existing imports to avoid duplicates
        existing_imports = set()
        for line in lines:
            if line.strip().startswith('import'):
                # Extract the interface name from the import statement
                match = re.search(r'import\s*{\s*(\w+)\s*}\s*from', line)
                if match:
                    existing_imports.add(match.group(1))
        
        # Only add imports for interfaces that don't already exist
        new_imports = [name for name in interfaces.keys() if name not in existing_imports]
        if new_imports:
            import_statements = "\n".join(
                f'import {{{name}}} from "test/interfaces/{name}.sol";'
                for name in new_imports
            )
            
            # Find the last import statement or contract declaration
            last_import_idx = -1
            contract_idx = -1
            for i, line in enumerate(lines):
                if line.strip().startswith('import'):
                    last_import_idx = i
                elif line.strip().startswith('contract'):
                    contract_idx = i
                    break
            
            # Determine where to insert the new imports
            if last_import_idx >= 0:
                # Insert after last import
                insert_idx = last_import_idx + 1
            else:
                # Insert before contract with one empty line
                insert_idx = contract_idx
            
            # Insert the imports with proper spacing
            if last_import_idx >= 0:
                # Add after last import
                lines.insert(insert_idx, import_statements)
            else:
                # Add before contract with one empty line
                lines.insert(insert_idx, '')
                lines.insert(insert_idx, import_statements)
            
            content = '\n'.join(lines)
        
        # Replace @ directives with actual interface names
        for interface_name in interfaces.keys():
            pattern = f'@{interface_name}'
            content = re.sub(pattern, interface_name, content)
        
        self.script_path.write_text(content)
    
    @handle_errors(error_type=ScriptError)
    def _ensure_interface_name_matches(self, interface_path: Path, expected_name: str) -> None:
        """Ensure the interface name in the file matches the expected name."""
        if not interface_path.exists():
            return
            
        content = interface_path.read_text()
        
        # Find the interface definition
        interface_pattern = r'interface\s+(\w+)\s*{'
        match = re.search(interface_pattern, content)
        
        if not match:
            return
            
        actual_name = match.group(1)
        if actual_name != expected_name:
            # Replace the interface name in the file
            content = content.replace(f"interface {actual_name}", f"interface {expected_name}")
            interface_path.write_text(content)
    
    @handle_errors(error_type=ScriptError)
    def clean_interfaces(self) -> None:
        """Remove all injected interfaces from the script."""
        if self.verbose:
            click.echo("Cleaning interfaces from script")
        content = self.script_path.read_text()
        
        # Remove import statements
        content = re.sub(r'import "src/test/interfaces/.*\.sol";\n?', '', content)
        
        # Remove interface declarations
        content = re.sub(r'interface \w+ \{\n.*?\n\}\n?', '', content, flags=re.DOTALL)
        
        self.script_path.write_text(content)
    
    @handle_errors(error_type=ScriptError)
    def check_broadcast_block(self, post: bool, skip_broadcast_check: bool = False) -> None:
        """
        Check for vm.startBroadcast in the contract and warn if not found and post is true.
        Ask the user if they want to proceed anyway.
        """
        if skip_broadcast_check:
            return
            
        content = self.script_path.read_text()
        lines = content.split('\n')

        # Flag to indicate if startBroadcast is found outside comments
        start_broadcast_found = False

        for line in lines:
            stripped_line = line.strip()

            # Skip comment lines
            if stripped_line.startswith('//') or stripped_line.startswith('/*') or stripped_line.startswith('*'):
                continue

            # Check for vm.startBroadcast outside of comments
            if 'vm.startBroadcast' in stripped_line:
                start_broadcast_found = True
                break

        if not start_broadcast_found and post:
            console.print("\n[yellow]WARNING:[/yellow] No broadcast block found in your script.")
            console.print("Scripts should be wrapped in a broadcast block for proper functionality:")
            console.print("  • Add a [bold]vm.startBroadcast()[/bold] block in your run() function")
            console.print("\nTo suppress this warning, you can:")
            console.print("  • Use the [bold]--skip-broadcast-check[/bold] option")
            console.print("  • Add [bold]skip_broadcast_check = true[/bold] in the safe section of your config file")
            
            if not click.confirm("\nDo you want to proceed anyway?", default=False):
                raise ScriptError("Operation cancelled by user due to missing broadcast block in script") 