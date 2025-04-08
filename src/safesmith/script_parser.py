"""Script parsing and modification"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Match, Union

import click
from safesmith.interface_manager import InterfaceManager
from safesmith.settings import SafesmithSettings
from safesmith.errors import ScriptError, handle_errors

class ScriptParser:
    """Parser for Foundry scripts that handles interface directives."""
    
    # Regex pattern for @ directive detection, ensuring it's not part of a comment
    INTERFACE_PATTERN = r'@([A-Z]\w+)'
    
    def __init__(self, script_path: Path, verbose: bool = False):
        """Initialize the parser with a script path."""
        self.script_path = script_path
        self.verbose = verbose
        if not self.script_path.exists():
            raise ScriptError(f"Script not found: {script_path}")
        self.original_content = self.script_path.read_text()
        self.processed_interfaces = {}
    
    @handle_errors(error_type=ScriptError)
    def parse_interfaces(self) -> Dict[str, str]:
        """Parse the script and extract interface directives."""
        content = self.script_path.read_text()
        
        # Find all potential interface directives
        interfaces = {}
        lines = content.split('\n')
        
        # Track contract state
        in_contract = False
        current_contract = None
        
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
            
            # Process directives within contracts
            if in_contract:
                # Look for @ directives
                matches = re.finditer(self.INTERFACE_PATTERN, line)
                for match in matches:
                    interface_name = match.group(1)
                    
                    # Get the address from the same line
                    address_match = re.search(r'0x[a-fA-F0-9]{40}', line)
                    if address_match:
                        address = address_match.group(0)
                        interfaces[interface_name] = address
        
        if self.verbose and interfaces:
            click.echo(f"Found {len(interfaces)} interfaces in script")
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
    def update_script(self, interfaces: Dict[str, str]) -> None:
        """Update the script with the actual interface names."""
        if self.verbose:
            click.echo("Updating script with interface imports")
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
        content = re.sub(r'import "src/interfaces/.*\.sol";\n?', '', content)
        
        # Remove interface declarations
        content = re.sub(r'interface \w+ \{\n.*?\n\}\n?', '', content, flags=re.DOTALL)
        
        self.script_path.write_text(content)
    
    @handle_errors(error_type=ScriptError)
    def check_broadcast_block(self, post: bool) -> None:
        """Check for vm.startBroadcast in the contract and throw an error if not found and post is true."""
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
            raise ScriptError("Script must be wrapped in a broadcast block") 