"""Interface management for fwrap."""

import json
import subprocess
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, Any, List
import requests
import tempfile
import re

from rich.console import Console

console = Console()

class InterfaceManager:
    """Manages Ethereum contract interfaces for Foundry scripts."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize with configuration options."""
        # Local interfaces path (relative to project)
        self.local_path = Path(config.get("interfaces", {}).get("local_path", "interfaces"))
        self.local_path.mkdir(exist_ok=True)
        
        # Global interfaces path
        default_global_path = Path.home() / ".config" / "fwrap" / "interfaces"
        self.global_path = Path(config.get("interfaces", {}).get("global_path", default_global_path))
        self.global_path.mkdir(parents=True, exist_ok=True)
        
        # API keys for contract explorers
        self.etherscan_api_key = config.get("api_keys", {}).get(
            "etherscan", os.environ.get("ETHERSCAN_API_KEY", "")
        )
        
        # Temporary directory for downloaded files
        self.temp_dir = Path(tempfile.mkdtemp())
    
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
                raise RuntimeError("cast command not found in PATH")
            
            # Verify cast works
            result = subprocess.run(
                ["cast", "--version"],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                raise RuntimeError("cast command failed")
                
        except Exception as e:
            console.print("[red]Error: Foundry's cast command is not available.[/red]")
            console.print("\nPlease install Foundry by running:")
            console.print("curl -L https://foundry.paradigm.xyz | bash")
            console.print("source ~/.bashrc  # or restart your terminal")
            console.print("foundryup")
            raise RuntimeError("Foundry is required but not installed")
    
    def _load_cache(self) -> None:
        """Load the interface cache from disk."""
        try:
            if self.cache_path.exists():
                with open(self.cache_path, "r") as f:
                    self.cache = json.load(f)
            else:
                self.cache = {}
        except Exception as e:
            console.print(f"[yellow]Warning: Could not load cache: {str(e)}[/yellow]")
            self.cache = {}
    
    def _save_cache(self) -> None:
        """Save the interface cache to disk."""
        try:
            with open(self.cache_path, "w") as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            console.print(f"[yellow]Warning: Could not save cache: {str(e)}[/yellow]")
    
    def _get_interface_paths(self, interface_name: str) -> Tuple[Path, Path]:
        """Get both local and global paths for an interface."""
        local_path = self.local_path / f"{interface_name}.sol"
        global_path = self.global_path / f"{interface_name}.sol"
        return local_path, global_path
    
    def process_interface(self, interface_name: str, address: str) -> Path:
        """
        Process an interface for a given address. Look in local and global cache,
        download from Etherscan if needed.
        """
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
        
        # Generate interface using cast
        try:
            self._generate_interface(interface_name, address)
            return local_file
        except Exception as e:
            console.print(f"[yellow]Warning: Failed to generate interface using cast: {e}[/yellow]")
            
            # Fall back to downloading just the ABI from Etherscan
            abi = self._download_abi_from_etherscan(address)
            if abi:
                self._create_interface_from_abi(local_file, interface_name, abi)
                return local_file
            
            # Create a default interface if all methods fail
            console.print(f"[red]Failed to generate interface for {interface_name}[/red]")
            return local_file
    
    def _copy_to_local(self, source_file: Path, interface_name: str) -> None:
        """Copy interface file to local directory, ensuring it has the correct interface name."""
        # Read the source file
        content = source_file.read_text()
        
        # Write to local file with the correct interface name
        local_file = self.local_path / f"{interface_name}.sol"
        self._write_interface_file(local_file, content, interface_name)
    
    def _write_interface_file(self, file_path: Path, content: str, interface_name: str) -> None:
        """Write interface file with the correct interface name."""
        # Ensure the directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Find the existing interface name in the content
        interface_pattern = r'interface\s+(\w+)'
        match = re.search(interface_pattern, content)
        
        if match:
            # Replace the existing interface name with the requested one
            existing_name = match.group(1)
            content = content.replace(f"interface {existing_name}", f"interface {interface_name}")
        
        # Write the modified content
        file_path.write_text(content)
    
    def _download_from_etherscan(self, address: str) -> Optional[str]:
        """
        Download contract ABI and source code from Etherscan.
        Returns the source code if successful, None otherwise.
        """
        if not self.etherscan_api_key:
            print("Warning: Etherscan API key not provided. Using default limited access.")
        
        # First, get the contract ABI
        url = f"https://api.etherscan.io/api?module=contract&action=getabi&address={address}&apikey={self.etherscan_api_key}"
        response = requests.get(url)
        
        if response.status_code != 200:
            print(f"Error accessing Etherscan API: {response.status_code}")
            return None
        
        data = response.json()
        if data["status"] != "1" or data["message"] != "OK":
            print(f"Error from Etherscan API: {data['message']}")
            return None
        
        # Now get the source code
        url = f"https://api.etherscan.io/api?module=contract&action=getsourcecode&address={address}&apikey={self.etherscan_api_key}"
        response = requests.get(url)
        
        if response.status_code != 200:
            print(f"Error accessing Etherscan API: {response.status_code}")
            return None
        
        data = response.json()
        if data["status"] != "1" or data["message"] != "OK":
            print(f"Error from Etherscan API: {data['message']}")
            return None
        
        if not data["result"] or not data["result"][0]["SourceCode"]:
            print("No source code available for this contract.")
            return None
        
        # Return the source code
        return data["result"][0]["SourceCode"]
    
    def _create_default_interface(self, file_path: Path, interface_name: str) -> None:
        """Create a default interface if download fails."""
        content = f"""// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.4;

interface {interface_name} {{
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
    
    def clear_cache(self) -> None:
        """Clear the global interface cache."""
        for file in self.global_path.glob("*.sol"):
            file.unlink()

    @property
    def cache_path(self) -> str:
        """Return the path to the global cache directory."""
        return str(self.global_path)

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

        raise FileNotFoundError("Could not find a working `cast` executable.")

    def _generate_interface(self, interface_name: str, address: str) -> None:
        """Generate a new interface using cast."""
        local_path, global_path = self._get_interface_paths(interface_name)
        
        # Check if file exists and handle overwrite
        if global_path.exists() and not self.config.get("interfaces", {}).get("overwrite", False):
            if not console.input(f"Interface '{interface_name}.sol' already exists globally. Overwrite? (y/N) ").lower().startswith("y"):
                # Instead of raising an error, copy the existing interface to the local project
                console.print(f"[yellow]Using existing interface {interface_name} from global cache[/yellow]")
                self.local_path.mkdir(parents=True, exist_ok=True)
                local_path.write_text(global_path.read_text())
                return
        
        # Find cast executable in common locations
        cast_paths = [
            Path.home() / ".foundry" / "bin" / "cast",  # Most common location
            Path("/usr/local/bin/cast"),
            Path("/usr/bin/cast"),
            Path(os.environ.get("HOME", "")) / ".foundry" / "bin" / "cast"  # Extra safe path
        ]
        
        # Find the first cast that exists
        cast_executable = None
        for path in cast_paths:
            if path.exists():
                cast_executable = str(path)
                break
        
        if not cast_executable:
            console.print("[red]Error: Could not find cast executable[/red]")
            console.print("Looked in:")
            for path in cast_paths:
                console.print(f"- {path}")
            raise RuntimeError("Cast executable not found")
        
        try:
            # Generate to global path first with full path to cast
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
                raise RuntimeError(f"Failed to generate interface: {result.stderr}")
            
            # Copy to local path
            self.local_path.mkdir(parents=True, exist_ok=True)
            local_path.write_text(global_path.read_text())
            
            # Include interface name in the output message
            console.print(f"[green]Generated interface {interface_name} for {address}[/green]")
            
        except Exception as e:
            console.print(f"[red]Error running cast: {str(e)}[/red]")
            console.print(f"PATH environment: {os.environ.get('PATH', 'not set')}")
            console.print(f"Cast executable path: {cast_executable}")
            raise RuntimeError(f"Error generating interface: {str(e)}")

    def _ensure_interface_file_exists(self, file_path: Path, interface_name: str) -> None:
        """
        Check if the interface file content is valid Solidity and not JSON.
        Fix it if needed.
        """
        if not file_path.exists():
            return
        
        content = file_path.read_text()
        
        # Check if the content is actually JSON (has the content property format)
        if content.strip().startswith('{') and ('"content"' in content or '"settings"' in content):
            console.print(f"[yellow]Warning: Interface file {file_path} contains JSON data instead of Solidity.[/yellow]")
            
            try:
                # Try to extract ABI from the JSON if possible
                import json
                json_data = json.loads(content)
                
                # Create a default interface since we can't extract from this JSON
                self._create_default_interface(file_path, interface_name)
                console.print(f"[green]Fixed interface file {file_path}[/green]")
                
            except Exception as e:
                console.print(f"[red]Failed to parse JSON in interface file: {e}[/red]")
                # Create a default interface
                self._create_default_interface(file_path, interface_name)

    def _download_abi_from_etherscan(self, address: str) -> Optional[str]:
        """
        Download contract ABI from Etherscan.
        Returns the ABI if successful, None otherwise.
        """
        if not self.etherscan_api_key:
            console.print("[yellow]Warning: Etherscan API key not provided. Using default limited access.[/yellow]")
        
        # Get the contract ABI
        url = f"https://api.etherscan.io/api?module=contract&action=getabi&address={address}&apikey={self.etherscan_api_key}"
        
        try:
            response = requests.get(url)
            
            if response.status_code != 200:
                console.print(f"[red]Error accessing Etherscan API: {response.status_code}[/red]")
                return None
            
            data = response.json()
            if data["status"] != "1" or data["message"] != "OK":
                console.print(f"[red]Error from Etherscan API: {data['message']}[/red]")
                return None
            
            return data["result"]
            
        except Exception as e:
            console.print(f"[red]Error fetching ABI from Etherscan: {str(e)}[/red]")
            return None

    def _create_interface_from_abi(self, file_path: Path, interface_name: str, abi_json: str) -> None:
        """
        Create a Solidity interface file from an ABI JSON string.
        """
        try:
            import json
            abi = json.loads(abi_json)
            
            # Generate Solidity interface content
            content = [
                "// SPDX-License-Identifier: MIT",
                "pragma solidity ^0.8.0;",
                "",
                f"interface {interface_name} {{",
            ]
            
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
                        inputs.append(f"{param_type} {param_name}")
                    
                    # Process outputs
                    outputs = []
                    for output_param in item.get("outputs", []):
                        outputs.append(output_param.get("type", ""))
                    
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
                    
                    func_signature = f"    function {func_name}({', '.join(inputs)}) external{func_type}{returns_str};"
                    content.append(func_signature)
                
                # Process events
                elif item.get("type") == "event":
                    event_name = item.get("name", "")
                    
                    # Skip if no name
                    if not event_name:
                        continue
                    
                    # Process inputs
                    inputs = []
                    for input_param in item.get("inputs", []):
                        param_type = input_param.get("type", "")
                        param_name = input_param.get("name", "arg")
                        if input_param.get("indexed", False):
                            inputs.append(f"{param_type} indexed {param_name}")
                        else:
                            inputs.append(f"{param_type} {param_name}")
                    
                    # Build the event signature
                    event_signature = f"    event {event_name}({', '.join(inputs)});"
                    content.append(event_signature)
            
            # Close the interface
            content.append("}")
            
            # Write to file
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("\n".join(content))
            console.print(f"[green]Created interface {interface_name} from ABI[/green]")
            
        except Exception as e:
            console.print(f"[red]Error creating interface from ABI: {str(e)}[/red]")