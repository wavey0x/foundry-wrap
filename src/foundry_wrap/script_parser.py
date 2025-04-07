"""Script parsing and modification for foundry-wrap."""

import re
from pathlib import Path
from typing import Dict, List, Optional, Match, Union

import click
from foundry_wrap.interface_manager import InterfaceManager
from foundry_wrap.settings import FoundryWrapSettings

class ScriptParser:
    """Parser for Foundry scripts that handles interface directives."""
    
    # Regex patter for @ directive detection, ensuring it's not part of a comment
    INTERFACE_PATTERN = r'@([A-Z]\w+)'
    
    def __init__(self, script_path: Path, verbose: bool = False):
        """Initialize the parser with a script path."""
        self.script_path = script_path
        self.verbose = verbose
        if self.verbose:
            click.echo("Initializing ScriptParser with pattern: @([A-Z]\\w+)")
        if not self.script_path.exists():
            raise FileNotFoundError(f"Script not found: {script_path}")
        self.original_content = self.script_path.read_text()
        self.processed_interfaces = {}
    
    def parse_interfaces(self) -> Dict[str, str]:
        """Parse the script and extract interface directives."""
        if self.verbose:
            click.echo(f"Reading script content from {self.script_path}")
        content = self.script_path.read_text()
        if self.verbose:
            click.echo(f"Script content before processing:\n{content}")
        
        if self.verbose:
            click.echo("Searching for interface directives...")
        
        # Phase 1: Find all potential interface directives
        interfaces = {}
        lines = content.split('\n')
        
        # Track contract state
        in_contract = False
        current_contract = None
        
        for i, line in enumerate(lines):
            line = line.strip()
            
            # Skip empty lines
            if not line:
                continue
                
            # Skip comment lines
            if line.startswith('//') or line.startswith('/*') or line.startswith('*'):
                if self.verbose:
                    click.echo(f"Skipping comment line {i+1}: {line}")
                continue
            
            # Check for contract declaration
            if line.startswith('contract') or line.startswith('interface'):
                in_contract = True
                current_contract = line.split()[1]  # Get contract name
                if self.verbose:
                    click.echo(f"Entering contract {current_contract} on line {i+1}")
                continue
                
            # Check for contract end
            if line.startswith('}'):
                if in_contract:
                    if self.verbose:
                        click.echo(f"Exiting contract {current_contract} on line {i+1}")
                    in_contract = False
                    current_contract = None
                continue
            
            # Process directives within contracts
            if in_contract:
                if self.verbose:
                    click.echo(f"Processing line {i+1} in contract {current_contract}: {line}")
                
                # Look for @ directives
                matches = re.finditer(self.INTERFACE_PATTERN, line)
                for match in matches:
                    interface_name = match.group(1)
                    if self.verbose:
                        click.echo(f"Found interface directive in contract {current_contract}: {interface_name}")
                    
                    # Get the address from the same line
                    address_match = re.search(r'0x[a-fA-F0-9]{40}', line)
                    if address_match:
                        address = address_match.group(0)
                        if self.verbose:
                            click.echo(f"Found address for {interface_name}: {address}")
                        interfaces[interface_name] = address
                    else:
                        if self.verbose:
                            click.echo(f"Warning: No address found for interface {interface_name}")
        
        if self.verbose:
            click.echo(f"Found {len(interfaces)} interfaces: {interfaces}")
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
    
    def update_script(self, interfaces: Dict[str, str]) -> None:
        """Update the script with the actual interface names."""
        if self.verbose:
            click.echo("Updating script with interface names...")
        content = self.script_path.read_text()
        
        # Add import statements with named imports
        import_statements = "\n".join(
            f'import {{{name}}} from "interfaces/{name}.sol";'
            for name in interfaces.keys()
        )
        
        # Split content into lines
        lines = content.split('\n')
        
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
            if self.verbose:
                click.echo(f"Replacing {pattern} with {interface_name}")
            content = re.sub(pattern, interface_name, content)
        
        if self.verbose:
            click.echo(f"Updated script content:\n{content}")
        self.script_path.write_text(content)
    
    def _ensure_interface_name_matches(self, interface_path: Path, expected_name: str) -> None:
        """Ensure the interface name in the file matches the expected name."""
        if not interface_path.exists():
            return
            
        content = interface_path.read_text()
        
        # Find the interface definition
        interface_pattern = r'interface\s+(\w+)\s*{'
        match = re.search(interface_pattern, content)
        
        if not match:
            if self.verbose:
                click.echo(f"Warning: Could not find interface definition in {interface_path}")
            return
            
        actual_name = match.group(1)
        if actual_name != expected_name:
            if self.verbose:
                click.echo(f"Updating interface name from {actual_name} to {expected_name}")
            # Replace the interface name in the file
            content = content.replace(f"interface {actual_name}", f"interface {expected_name}")
            interface_path.write_text(content)
    
    def clean_interfaces(self) -> None:
        """Remove all injected interfaces from the script."""
        if self.verbose:
            click.echo("Cleaning interfaces from script...")
        content = self.script_path.read_text()
        
        # Remove import statements
        content = re.sub(r'import "src/interfaces/.*\.sol";\n?', '', content)
        
        # Remove interface declarations
        content = re.sub(r'interface \w+ \{\n.*?\n\}\n?', '', content, flags=re.DOTALL)
        
        if self.verbose:
            click.echo(f"Cleaned script content:\n{content}")
        self.script_path.write_text(content) 