"""Script parsing and modification for foundry-wrap."""

import re
from pathlib import Path
from typing import Dict, List, Optional, Match, Union

from foundry_wrap.interface_manager import InterfaceManager
from foundry_wrap.settings import FoundryWrapSettings

class ScriptParser:
    """Parser for Foundry script files to extract and process interface directives."""
    
    # Updated regex pattern to detect FW- directives in various forms
    INTERFACE_PATTERN = r'(FW-\w+)'
    
    def __init__(self, script_path: Path):
        """Initialize with the path to a script file."""
        self.script_path = Path(script_path)
        if not self.script_path.exists():
            raise FileNotFoundError(f"Script not found: {script_path}")
        self.original_content = self.script_path.read_text()
        self.processed_interfaces = {}
    
    def parse_interfaces(self) -> Dict[str, str]:
        """
        Parse the script file and extract required interfaces and their addresses.
        Returns a dictionary mapping interface names to addresses.
        """
        interfaces = {}
        
        # Find all interface directives in the form FW-{Interface}
        fw_matches = re.finditer(self.INTERFACE_PATTERN, self.original_content)
        
        # Collect all unique interface directives
        interface_directives = set()
        for match in fw_matches:
            interface_directives.add(match.group(1))
        
        # Process each unique directive
        for directive in interface_directives:
            interface_name = directive[3:]  # Remove "FW-" prefix
            
            # Look for the directive being used with an address
            # Try multiple patterns to catch different usages
            patterns = [
                # Pattern for FW-IERC20 token = FW-IERC20(0x123...)
                rf'{directive}\s+\w+\s*=\s*{directive}\(([^)]+)\)',
                # Pattern for token = FW-IERC20(0x123...)
                rf'\w+\s*=\s*{directive}\(([^)]+)\)',
                # Pattern for FW-IERC20(0x123...)
                rf'{directive}\(([^)]+)\)'
            ]
            
            address = None
            for pattern in patterns:
                match = re.search(pattern, self.original_content)
                if match:
                    address = match.group(1).strip()
                    break
            
            if address:
                interfaces[interface_name] = address
                self.processed_interfaces[interface_name] = True
        
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
        """
        Update the script file with processed interfaces.
        Replaces FW-{Interface} directives with the actual interface name
        and ensures imports use named import syntax.
        """
        updated_content = self.original_content
        
        # Get an instance of the InterfaceManager using the setting system
        settings = FoundryWrapSettings()
        interface_manager = InterfaceManager(settings)
        
        # First, add imports for all interfaces at the top of the file
        import_statements = []
        for interface_name, address in interfaces.items():
            # Use the interface manager to process the interface, ensuring the correct name
            interface_path = interface_manager.process_interface(interface_name, address)
            
            # Verify the interface file has the correct interface name
            self._ensure_interface_name_matches(interface_path, interface_name)
            
            import_statements.append(f'import {{ {interface_name} }} from "interfaces/{interface_name}.sol";')
        
        # Find position to insert imports (after pragma statements)
        lines = updated_content.split('\n')
        insert_pos = 0
        for i, line in enumerate(lines):
            if re.match(r'^\s*(pragma|//\s*SPDX)', line):
                insert_pos = i + 1
        
        # Insert all import statements
        for stmt in reversed(import_statements):
            # Only add if it doesn't already exist
            if stmt not in updated_content:
                lines.insert(insert_pos, stmt)
        
        # Remove all trailing blank lines
        while lines and not lines[-1].strip():
            lines.pop()
        
        # Create the updated content
        updated_content = '\n'.join(lines)
        
        # Then replace all FW- directives
        for interface_name in interfaces.keys():
            directive = f"FW-{interface_name}"
            updated_content = updated_content.replace(directive, interface_name)
        
        # Ensure exactly one newline at the end of file
        updated_content = updated_content.rstrip('\n') + '\n'
        
        # Write the updated content back to the file
        self.script_path.write_text(updated_content)
    
    def _ensure_interface_name_matches(self, interface_path: Path, expected_name: str) -> None:
        """
        Ensure that the interface name in the file matches the expected name.
        This is critical for the correct compilation when we replace FW- directives.
        """
        if not interface_path.exists():
            return
        
        content = interface_path.read_text()
        
        # Find the interface definition
        interface_pattern = r'interface\s+(\w+)'
        match = re.search(interface_pattern, content)
        
        if not match:
            # No interface found, this is unusual
            print(f"Warning: Could not find interface definition in {interface_path}")
            return
        
        actual_name = match.group(1)
        if actual_name != expected_name:
            # Replace the interface name with the expected one
            print(f"Fixing interface name in {interface_path}: {actual_name} → {expected_name}")
            content = content.replace(f"interface {actual_name}", f"interface {expected_name}")
            interface_path.write_text(content)
    
    def clean_interfaces(self) -> None:
        """Restore the original script content, removing injected interfaces."""
        self.script_path.write_text(self.original_content) 