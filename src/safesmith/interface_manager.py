"""Interface management"""

import json
import subprocess
import shutil
from pathlib import Path
from typing import Dict, Optional, Tuple, Any, Union, List
import requests
import tempfile
import re
import importlib.resources as pkg_resources
import os
import sys
from web3 import Web3
from eth_utils import to_checksum_address

from rich.console import Console
from safesmith.settings import SafesmithSettings
from safesmith.errors import InterfaceError, handle_errors, NetworkError

console = Console()

# EIP1967 storage slots
EIP1967_IMPLEMENTATION_SLOT = Web3.keccak(text="eip1967.proxy.implementation").hex()
EIP1967_IMPLEMENTATION_SLOT_MINUS_1 = hex(int(EIP1967_IMPLEMENTATION_SLOT, 16) - 1)
EIP1967_BEACON_SLOT = Web3.keccak(text="eip1967.proxy.beacon").hex()

# EIP1822 storage slot
EIP1822_PROXIABLE_SLOT = Web3.keccak(text="PROXIABLE").hex()

def get_storage_at(web3: Web3, address: str, slot: str) -> str:
    """Get storage at a specific slot for an address."""
    try:
        return web3.eth.get_storage_at(address, int(slot, 16)).hex()
    except Exception as e:
        return ""

def is_proxy_implementation(web3: Web3, address: str) -> bool:
    """Check if an address is a proxy implementation."""
    # Check EIP1967 implementation slot
    impl = get_storage_at(web3, address, EIP1967_IMPLEMENTATION_SLOT)
    if impl and int(impl, 16) != 0:
        return True
        
    # Check EIP1967 implementation slot minus 1
    impl_minus_1 = get_storage_at(web3, address, EIP1967_IMPLEMENTATION_SLOT_MINUS_1)
    if impl_minus_1 and int(impl_minus_1, 16) != 0:
        return True
        
    # Check EIP1967 beacon slot
    beacon = get_storage_at(web3, address, EIP1967_BEACON_SLOT)
    if beacon and int(beacon, 16) != 0:
        return True
        
    # Check EIP1822 slot
    proxiable = get_storage_at(web3, address, EIP1822_PROXIABLE_SLOT)
    if proxiable and int(proxiable, 16) != 0:
        return True
        
    return False

def get_implementation_address(web3: Web3, address: str) -> Optional[str]:
    """Get the implementation address for a proxy contract."""
    # Try EIP1967 implementation slot first
    impl = get_storage_at(web3, address, EIP1967_IMPLEMENTATION_SLOT)
    if impl and int(impl, 16) != 0:
        return to_checksum_address("0x" + impl[-40:])
        
    # Try EIP1967 implementation slot minus 1
    impl_minus_1 = get_storage_at(web3, address, EIP1967_IMPLEMENTATION_SLOT_MINUS_1)
    if impl_minus_1 and int(impl_minus_1, 16) != 0:
        return to_checksum_address("0x" + impl_minus_1[-40:])
        
    # Try EIP1967 beacon slot
    beacon = get_storage_at(web3, address, EIP1967_BEACON_SLOT)
    if beacon and int(beacon, 16) != 0:
        return to_checksum_address("0x" + beacon[-40:])
        
    # Try EIP1822 slot
    proxiable = get_storage_at(web3, address, EIP1822_PROXIABLE_SLOT)
    if proxiable and int(proxiable, 16) != 0:
        return to_checksum_address("0x" + proxiable[-40:])
        
    return None

def merge_abis(proxy_abi: List[Dict], impl_abi: List[Dict]) -> List[Dict]:
    """Merge proxy and implementation ABIs, removing duplicates."""
    # Create a set of function signatures to track duplicates
    seen_signatures = set()
    merged_abi = []
    
    # Helper to create function signature
    def get_signature(item: Dict) -> str:
        if item.get("type") != "function":
            return ""
        inputs = [f"{i['type']}" for i in item.get("inputs", [])]
        return f"{item['name']}({','.join(inputs)})"
    
    # Add all functions from both ABIs, skipping duplicates
    for item in proxy_abi + impl_abi:
        if item.get("type") == "function":
            signature = get_signature(item)
            if signature and signature not in seen_signatures:
                seen_signatures.add(signature)
                merged_abi.append(item)
        else:
            merged_abi.append(item)
    
    return merged_abi

class InterfaceManager:
    """Manages Ethereum contract interfaces for Foundry scripts."""
    
    def __init__(self, settings: Union[SafesmithSettings, Dict[str, Any]]):
        """
        Initialize with settings.
        
        Args:
            settings: Either a SafesmithSettings instance or a dictionary for backward compatibility
        """
        # Convert dictionary to settings if needed (backward compatibility)
        if isinstance(settings, dict):
            # Create a configuration dictionary from the legacy format
            config_dict = {}
            # Map the old dictionary format to our new nested structure
            if "interfaces" in settings:
                config_dict["interfaces"] = settings["interfaces"]
            if "api_keys" in settings and "etherscan" in settings["api_keys"]:
                config_dict["etherscan"] = {"api_key": settings["api_keys"]["etherscan"]}
            # Create a settings object
            self.settings = SafesmithSettings(**config_dict)
        else:
            self.settings = settings
        
        # Local interfaces path (relative to project)
        self.local_path = Path(self.settings.interfaces.local_path)
        self.local_path.mkdir(exist_ok=True)
        
        # Global interfaces path
        self.global_path = Path(self.settings.interfaces.global_path)
        self.global_path.mkdir(parents=True, exist_ok=True)
        
        # Presets paths
        self.presets_path = Path(self.settings.presets.path)
        self.presets_path.mkdir(parents=True, exist_ok=True)
        self.presets_index_file = Path(self.settings.presets.index_file)
        
        # API keys for contract explorers
        self.etherscan_api_key = self.settings.etherscan.api_key
        
        # Store the entire config for config-dependent methods
        self.config = self.settings.model_dump() if hasattr(self.settings, 'model_dump') else settings
        
        # Temporary directory for downloaded files
        self.temp_dir = Path(tempfile.mkdtemp())
        
        # Initialize presets if needed
        self._init_presets()
    
    @handle_errors(error_type=InterfaceError)
    def _init_presets(self) -> None:
        """Initialize preset interfaces directory with package presets."""
        if not self.presets_path.exists():
            self.presets_path.mkdir(parents=True, exist_ok=True)
            
        # Check if presets index exists
        if not self.presets_index_file.exists():
            # Copy preset interfaces from package to user directory
            self._copy_package_presets()
            
            # Generate the index file
            self.update_preset_index()
    
    @handle_errors(error_type=InterfaceError)
    def _copy_package_presets(self) -> None:
        """Copy preset interfaces from the package to the user's presets directory."""
        # Get the directory of this module
        module_dir = Path(__file__).parent
        package_presets_dir = module_dir / "presets"
        
        # Copy each preset interface
        if package_presets_dir.exists():
            for preset_file in package_presets_dir.glob("*.sol"):
                target_path = self.presets_path / preset_file.name
                if not target_path.exists():
                    # Read content from source
                    content = preset_file.read_text()
                    # Write to target location
                    target_path.write_text(content)
                    console.print(f"[green]Copied preset interface: {preset_file.stem}[/green]")
    
    @handle_errors(error_type=InterfaceError)
    def update_preset_index(self) -> None:
        """Update the preset index from both package and user presets."""
        presets = {}
        
        # Include user presets (from ~/.safesmith/presets/)
        for preset_file in self.presets_path.glob("*.sol"):
            presets[preset_file.stem] = str(preset_file)
        
        # Write the index file
        self.presets_index_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.presets_index_file, 'w') as f:
            json.dump(presets, f, indent=2)
        
        console.print(f"[green]Updated preset index with {len(presets)} interfaces[/green]")
    
    @handle_errors(error_type=InterfaceError)
    def load_preset_index(self) -> Dict[str, str]:
        """Load the preset index from disk."""
        if self.presets_index_file.exists():
            try:
                with open(self.presets_index_file, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                # If the file is corrupted, regenerate it
                self.update_preset_index()
                with open(self.presets_index_file, 'r') as f:
                    return json.load(f)
        else:
            # If the index doesn't exist, generate it
            self.update_preset_index()
            if self.presets_index_file.exists():
                with open(self.presets_index_file, 'r') as f:
                    return json.load(f)
            return {}
    
    @handle_errors(error_type=InterfaceError)
    def _get_preset_path(self, interface_name: str) -> Optional[Path]:
        """Get path to a preset interface by name."""
        presets = self.load_preset_index()
        if interface_name in presets:
            preset_path = Path(presets[interface_name])
            if preset_path.exists():
                return preset_path
        return None
    
    @handle_errors(error_type=InterfaceError)
    def _check_cast_availability(self) -> None:
        """Check if cast is available in the system."""
        try:
            # Try with full path first
            result = subprocess.run(
                ["which", "cast"],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                raise InterfaceError("cast command not found in PATH")
            
            # Verify cast works
            result = subprocess.run(
                ["cast", "--version"],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                raise InterfaceError("cast command failed")
                
        except Exception as e:
            console.print("[red]Error: Foundry's cast command is not available.[/red]")
            console.print("\nPlease install Foundry by running:")
            console.print("curl -L https://foundry.paradigm.xyz | bash")
            console.print("source ~/.bashrc  # or restart your terminal")
            console.print("foundryup")
            raise InterfaceError("Foundry is required but not installed", {"error": str(e)})
    
    @handle_errors(error_type=InterfaceError)
    def _load_cache(self) -> Dict[str, Any]:
        """Load the interface cache from disk."""
        if self.cache_path.exists():
            with open(self.cache_path, "r") as f:
                return json.load(f)
        return {}
    
    @handle_errors(error_type=InterfaceError)
    def _save_cache(self, cache: Dict[str, Any]) -> None:
        """Save the interface cache to disk."""
        with open(self.cache_path, "w") as f:
            json.dump(cache, f, indent=2)
    
    def _get_interface_paths(self, interface_name: str) -> Tuple[Path, Path]:
        """Get both local and global paths for an interface."""
        # Sanitize the interface name before creating paths
        sanitized_name = self.sanitize_interface_name(interface_name)
        local_path = self.local_path / f"{sanitized_name}.sol"
        global_path = self.global_path / f"{sanitized_name}.sol"
        return local_path, global_path
    
    @handle_errors(error_type=InterfaceError)
    def process_interface(self, interface_name: str, address: str = None) -> Path:
        """
        Process an interface for a given address or preset name.
        
        Args:
            interface_name: The name of the interface to process
            address: The contract address (optional for presets)
            
        Returns:
            Path to the processed interface file
        
        Raises:
            InterfaceError: If the interface cannot be processed
        """
        # First, check if this is a preset interface (no address or preset takes precedence)
        if address is None or self._get_preset_path(interface_name):
            preset_path = self._get_preset_path(interface_name)
            if preset_path:
                # Copy preset to local directory
                local_file = self.local_path / f"{interface_name}.sol"
                self._copy_to_local(preset_path, interface_name)
                return local_file
            elif address is None:
                # If no address and not a preset, raise an error
                raise InterfaceError(f"No address provided and {interface_name} is not a known preset. "
                                    f"Run 'safesmith sync-presets' if you've recently added this preset.")
        
        # If we get here, we're handling an address-based interface or a preset wasn't found
        
        # Try to find in local interfaces directory
        local_file = self.local_path / f"{interface_name}.sol"
        if local_file.exists():
            # Ensure it's a valid Solidity interface, not JSON data
            self._ensure_interface_file_exists(local_file, interface_name)
            return local_file
        
        # Try to find in global interfaces directory
        global_file = self.global_path / f"{interface_name}.sol"
        if global_file.exists():
            # Ensure it's a valid Solidity interface, not JSON data
            self._ensure_interface_file_exists(global_file, interface_name)
            # Copy to local directory, ensuring it has the correct interface name
            self._copy_to_local(global_file, interface_name)
            return local_file
        
        # Initialize Web3 with the RPC URL from settings
        web3 = Web3(Web3.HTTPProvider(self.settings.rpc.url))
        
        # Check if this is a proxy contract
        impl_address = get_implementation_address(web3, address)
        if impl_address:
            console.print(f"[white][dim]found implementation @ {impl_address}[/dim][/white]")
            
            # Try to get and merge ABIs from Etherscan
            proxy_abi = self._download_abi_from_etherscan(address)
            impl_abi = self._download_abi_from_etherscan(impl_address)
            
            if proxy_abi and impl_abi:
                # Merge the ABIs
                merged_abi = merge_abis(json.loads(proxy_abi), json.loads(impl_abi))
                # Create interface in both local and global directories
                self._create_interface_from_abi(local_file, interface_name, json.dumps(merged_abi))
                self._create_interface_from_abi(global_file, interface_name, json.dumps(merged_abi))
                return local_file
        
        # If not a proxy or Etherscan download failed, try cast
        try:
            self._generate_interface(interface_name, address)
            return local_file
        except InterfaceError as e:
            console.print(f"[yellow]Warning: Failed to generate interface using cast: {e}[/yellow]")
            
            # Fall back to downloading just the ABI from Etherscan
            abi = self._download_abi_from_etherscan(address)
            if abi:
                # Create interface in both local and global directories
                self._create_interface_from_abi(local_file, interface_name, abi)
                self._create_interface_from_abi(global_file, interface_name, abi)
                return local_file
            
            # Create a default interface if all methods fail
            console.print(f"[red]Failed to generate interface for {interface_name}[/red]")
            self._create_default_interface(local_file, interface_name)
            self._create_default_interface(global_file, interface_name)
            return local_file
    
    @handle_errors(error_type=InterfaceError)
    def _copy_to_local(self, source_file: Path, interface_name: str) -> None:
        """Copy interface file to local directory, ensuring it has the correct interface name."""
        # Read the source file
        content = source_file.read_text()
        
        # Write to local file with the correct interface name
        local_file = self.local_path / f"{interface_name}.sol"
        self._write_interface_file(local_file, content, interface_name)
    
    @handle_errors(error_type=InterfaceError)
    def _write_interface_file(self, file_path: Path, content: str, interface_name: str) -> None:
        """Write interface file with the correct interface name."""
        # Sanitize the interface name
        sanitized_name = self.sanitize_interface_name(interface_name)
        
        # Ensure the directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Find the existing interface name in the content
        interface_pattern = r'interface\s+(\w+)\s*{'
        match = re.search(interface_pattern, content)
        
        if match:
            # Replace the existing interface name with the sanitized one
            existing_name = match.group(1)
            if existing_name != sanitized_name:
                content = content.replace(f"interface {existing_name}", f"interface {sanitized_name}")
        
        # Write the modified content
        file_path.write_text(content)
    
    @handle_errors(error_type=NetworkError)
    def _download_from_etherscan(self, address: str) -> Optional[str]:
        """
        Download contract ABI and source code from Etherscan.
        Returns the source code if successful, None otherwise.
        """
        if not self.etherscan_api_key:
            console.print("[yellow]Warning: Etherscan API key not provided. Using default limited access.[/yellow]")
        
        # First, get the contract ABI
        url = f"https://api.etherscan.io/api?module=contract&action=getabi&address={address}&apikey={self.etherscan_api_key}"
        response = requests.get(url)
        
        if response.status_code != 200:
            console.print(f"[red]Error accessing Etherscan API: {response.status_code}[/red]")
            return None
        
        data = response.json()
        if data["status"] != "1" or data["message"] != "OK":
            console.print(f"[red]Error from Etherscan API: {data['message']}[/red]")
            return None
        
        # Now get the source code
        url = f"https://api.etherscan.io/api?module=contract&action=getsourcecode&address={address}&apikey={self.etherscan_api_key}"
        response = requests.get(url)
        
        if response.status_code != 200:
            console.print(f"[red]Error accessing Etherscan API: {response.status_code}[/red]")
            return None
        
        data = response.json()
        if data["status"] != "1" or data["message"] != "OK":
            console.print(f"[red]Error from Etherscan API: {data['message']}[/red]")
            return None
        
        if not data["result"] or not data["result"][0]["SourceCode"]:
            console.print("[yellow]No source code available for this contract.[/yellow]")
            return None
        
        # Return the source code
        return data["result"][0]["SourceCode"]
    
    @handle_errors(error_type=InterfaceError)
    def _create_default_interface(self, file_path: Path, interface_name: str) -> None:
        """Create a default interface if download fails."""
        # Sanitize the interface name
        sanitized_name = self.sanitize_interface_name(interface_name)
        
        content = f"""// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.4;

interface {sanitized_name} {{
    // Default interface with common ERC20 functions
    function balanceOf(address account) external view returns (uint256);
    function transfer(address recipient, uint256 amount) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transferFrom(address sender, address recipient, uint256 amount) external returns (bool);
    
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
}}
"""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
    
    @handle_errors(error_type=InterfaceError)
    def list_cached_interfaces(self) -> Dict[str, str]:
        """
        List all cached interfaces in both local and global directories.
        Returns a dictionary mapping interface names to their file paths.
        """
        cached = {}
        
        # Local interfaces - handle both absolute and relative paths
        for file in self.local_path.glob("*.sol"):
            try:
                # Try to get the relative path for nicer display
                rel_path = file.relative_to(Path.cwd())
                cached[file.stem] = str(rel_path)
            except ValueError:
                # If the file is not in the current directory, just use the full path
                cached[file.stem] = str(file)
        
        # Global interfaces
        for file in self.global_path.glob("*.sol"):
            if file.stem not in cached:  # Don't overwrite local interfaces
                cached[file.stem] = str(file)
        
        return cached
    
    @handle_errors(error_type=InterfaceError)
    def clear_cache(self) -> None:
        """Clear the global interface cache."""
        for file in self.global_path.glob("*.sol"):
            file.unlink()

    @property
    def cache_path(self) -> str:
        """Return the path to the global cache directory."""
        return str(self.global_path)

    @handle_errors(error_type=InterfaceError)
    def _find_cast_executable(self) -> str:
        """Find the cast executable and verify it works."""
        cast_path = shutil.which("cast")
        if cast_path:
            try:
                subprocess.run([cast_path, "--version"], capture_output=True, check=True)
                return cast_path
            except Exception:
                pass

        # Try known fallback paths
        fallback_paths = [
            Path.home() / ".foundry/bin/cast",
            Path("/usr/local/bin/cast"),
            Path("/usr/bin/cast"),
        ]
        for path in fallback_paths:
            if path.is_file():
                try:
                    subprocess.run([str(path), "--version"], capture_output=True, check=True)
                    return str(path)
                except Exception:
                    continue

        raise InterfaceError("Could not find a working `cast` executable")

    @handle_errors(error_type=InterfaceError)
    def _generate_interface(self, interface_name: str, address: str) -> None:
        """Generate a new interface using cast."""
        # Sanitize the interface name before any processing
        sanitized_name = self.sanitize_interface_name(interface_name)
        local_path, global_path = self._get_interface_paths(sanitized_name)
        
        # Check if file exists and handle overwrite
        if global_path.exists() and not self.settings.interfaces.overwrite:
            if not console.input(f"Interface '{sanitized_name}.sol' already exists globally. Overwrite? (y/N) ").lower().startswith("y"):
                # Instead of raising an error, copy the existing interface to the local project
                console.print(f"[yellow]Using existing interface {sanitized_name} from global cache[/yellow]")
                self.local_path.mkdir(parents=True, exist_ok=True)
                local_path.write_text(global_path.read_text())
                return
        
        # Find cast executable using the dedicated method
        try:
            cast_executable = self._find_cast_executable()
        except InterfaceError:
            console.print("[red]Error: Could not find cast executable[/red]")
            console.print("Make sure Foundry is installed. Run: curl -L https://foundry.paradigm.xyz | bash")
            raise
        
        # Generate to global path first
        self.global_path.mkdir(parents=True, exist_ok=True)
        
        # Create command with direct path to cast
        cmd = f"{cast_executable} interface -o {str(global_path)} {address}"
        
        # Run using a shell to ensure environment is properly set up
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            console.print(f"[red]Command failed:[/red] {cmd}")
            console.print(f"[red]Error output:[/red]\n{result.stderr}")
            raise InterfaceError(f"Failed to generate interface: {result.stderr}")
        
        # Read the generated interface
        content = global_path.read_text()
        
        # Find the interface name in the generated file
        interface_pattern = r'interface\s+([A-Za-z0-9_\s]+)\s*{'
        match = re.search(interface_pattern, content)
        
        if match:
            existing_name = match.group(1).strip()
            # Always use our sanitized name, regardless of what cast generated
            content = content.replace(f"interface {existing_name}", f"interface {sanitized_name}")
            global_path.write_text(content)
        
        # Copy to local path
        self.local_path.mkdir(parents=True, exist_ok=True)
        local_path.write_text(content)
        
        # Include interface name in the output message
        console.print(f"[green]Generated interface {sanitized_name} for {address}[/green]")

    @handle_errors(error_type=InterfaceError)
    def _ensure_interface_file_exists(self, file_path: Path, interface_name: str) -> None:
        """
        Check if the interface file content is valid Solidity and not JSON.
        Fix it if needed.
        """
        if not file_path.exists():
            return
        
        content = file_path.read_text()
        
        # Sanitize the interface name
        sanitized_name = self.sanitize_interface_name(interface_name)
        
        # Check if the content is actually JSON (has the content property format)
        if content.strip().startswith('{') and ('"content"' in content or '"settings"' in content):
            console.print(f"[yellow]Warning: Interface file {file_path} contains JSON data instead of Solidity.[/yellow]")
            
            # Create a default interface since we can't extract from this JSON
            self._create_default_interface(file_path, sanitized_name)
            console.print(f"[green]Fixed interface file {file_path}[/green]")
            return
            
        # Check if the interface name in the file matches the sanitized name
        interface_pattern = r'interface\s+([A-Za-z0-9_\s]+)\s*{'
        match = re.search(interface_pattern, content)
        
        if match:
            existing_name = match.group(1).strip()
            if existing_name != sanitized_name:
                # Replace the interface name in the file
                content = content.replace(f"interface {existing_name}", f"interface {sanitized_name}")
                file_path.write_text(content)

    @handle_errors(error_type=NetworkError)
    def _download_abi_from_etherscan(self, address: str) -> Optional[str]:
        """
        Download contract ABI from Etherscan.
        Returns the ABI if successful, None otherwise.
        """
        if not self.etherscan_api_key:
            console.print("[yellow]Warning: Etherscan API key not provided. Using default limited access.[/yellow]")
        
        # Get the contract ABI
        url = f"https://api.etherscan.io/api?module=contract&action=getabi&address={address}&apikey={self.etherscan_api_key}"
        
        response = requests.get(url)
        
        if response.status_code != 200:
            console.print(f"[red]Error accessing Etherscan API: {response.status_code}[/red]")
            return None
        
        data = response.json()
        if data["status"] != "1" or data["message"] != "OK":
            console.print(f"[red]Error from Etherscan API: {data['message']}[/red]")
            return None
        
        # Parse the ABI
        return data["result"]

    @handle_errors(error_type=InterfaceError)
    def _create_interface_from_abi(self, file_path: Path, interface_name: str, abi_json: str) -> None:
        """
        Create a Solidity interface file from an ABI JSON string.
        """
        import json
        abi = json.loads(abi_json)
        
        # Sanitize the interface name
        sanitized_name = self.sanitize_interface_name(interface_name)
        
        # Generate Solidity interface content
        content = [
            "// SPDX-License-Identifier: MIT",
            "pragma solidity ^0.8.0;",
            "",
            f"interface {sanitized_name} {{",
        ]
        
        # Helper function to add memory keyword where needed
        def add_memory_keyword(type_str: str) -> str:
            if type_str in ["string", "bytes"] or "[]" in type_str:
                return f"{type_str} memory"
            return type_str
        
        # Process functions from ABI
        for item in abi:
            if item.get("type") == "function":
                func_name = item.get("name", "")
                
                # Skip if no name
                if not func_name:
                    continue
                
                # Process inputs
                inputs = []
                for input_param in item.get("inputs", []):
                    param_type = input_param.get("type", "")
                    param_name = input_param.get("name", "arg")
                    # Add memory keyword for string, bytes, and array types
                    param_type = add_memory_keyword(param_type)
                    inputs.append(f"{param_type} {param_name}")
                
                # Process outputs
                outputs = []
                for output_param in item.get("outputs", []):
                    output_type = output_param.get("type", "")
                    # Add memory keyword for string, bytes, and array types
                    output_type = add_memory_keyword(output_type)
                    outputs.append(output_type)
                
                # Determine function type
                func_type = ""
                if item.get("stateMutability") == "view" or item.get("stateMutability") == "pure":
                    func_type = f" {item.get('stateMutability')}"
                elif not item.get("stateMutability") == "nonpayable":
                    func_type = f" {item.get('stateMutability')}"
                
                # Build the function signature
                returns_str = ""
                if outputs:
                    if len(outputs) == 1:
                        returns_str = f" returns ({outputs[0]})"
                    else:
                        returns_str = f" returns ({', '.join(outputs)})"
                
                # Add the function to the interface
                content.append(f"    function {func_name}({', '.join(inputs)}) external{func_type}{returns_str};")
        
        # Close the interface
        content.append("}")
        
        # Write the interface file
        final_content = "\n".join(content)
        file_path.write_text(final_content)

    def sanitize_interface_name(self, name: str) -> str:
        """
        Sanitize an interface name by removing spaces and special characters.
        Ensures the name is valid for use in filenames and Solidity imports.
        
        Args:
            name: The interface name to sanitize
            
        Returns:
            A sanitized version of the name suitable for filenames and imports
        """
        # Remove spaces and special characters, keep only alphanumeric and underscores
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '', name)
        
        # Ensure it starts with a letter (Solidity requirement)
        if not sanitized[0].isalpha():
            sanitized = 'I' + sanitized
            
        if name != sanitized:
            console.print(f"[yellow]Warning:[/yellow] Interface name '{name}' was sanitized to '{sanitized}'")
        
        return sanitized