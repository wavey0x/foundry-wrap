"""Configuration management for fwrap."""

import os
from pathlib import Path
from typing import Dict, Any, Optional

import toml
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get user's home directory
HOME_DIR = Path.home()
FWRAP_DIR = HOME_DIR / ".fwrap"
FWRAP_DIR.mkdir(exist_ok=True)

# Global config path
GLOBAL_CONFIG_PATH = HOME_DIR / ".fwrap" / "config.toml"

# Default configuration
DEFAULT_CONFIG = {
    "cache": {
        "path": str(FWRAP_DIR / "interface-cache.json"),
        "enabled": True,
    },
    "interfaces": {
        "global_path": str(FWRAP_DIR / "interfaces"),  # Global interfaces
        "local_path": "interfaces",  # Default project interfaces location
        "overwrite": False,
    },
    "etherscan": {
        "api_key": os.getenv("ETHERSCAN_API_KEY", ""),
    },
    "safe": {
        "safe_address": "",
        "safe_proposer": "",
    },
    "rpc": {
        "url": "https://eth.merkle.io",
    }
}

def create_default_global_config() -> None:
    """Create default global configuration file."""
    # Ensure the directory exists
    config_dir = GLOBAL_CONFIG_PATH.parent
    config_dir.mkdir(parents=True, exist_ok=True)
    
    # Default configuration
    default_config = {
        "interfaces": {
            "global_path": str(config_dir / "interfaces"),
            "local_path": "interfaces",
        },
        "safe": {
            "safe_address": "",
            "safe_proposer": "",
        },
        "rpc": {
            "url": "https://eth.merkle.io",
        }
    }
    
    # Create the interfaces directory as well
    interfaces_dir = config_dir / "interfaces"
    interfaces_dir.mkdir(exist_ok=True)
    
    # Write the config file
    with open(GLOBAL_CONFIG_PATH, "w") as f:
        toml.dump(default_config, f)

def load_config(config_path: Optional[str] = None, cli_options: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Load configuration from various sources in order of precedence:
    1. CLI options
    2. Local config file (.fwrap.toml in current directory)
    3. Custom config file (if specified)
    4. Global config file (~/.config/fwrap/config.toml)
    5. Default values
    """
    # Start with empty config
    config = {}
    
    # Create or load global config
    if not GLOBAL_CONFIG_PATH.exists():
        try:
            create_default_global_config()
            with open(GLOBAL_CONFIG_PATH, "r") as f:
                config.update(toml.load(f))
        except (toml.TomlDecodeError, OSError) as e:
            print(f"Warning: Error creating or reading global config file: {e}")
    else:
        try:
            with open(GLOBAL_CONFIG_PATH, "r") as f:
                config.update(toml.load(f))
        except (toml.TomlDecodeError, OSError) as e:
            print(f"Warning: Error reading global config file: {e}")
    
    # Load custom config if specified
    if config_path:
        try:
            with open(config_path, "r") as f:
                config.update(toml.load(f))
        except (toml.TomlDecodeError, OSError) as e:
            print(f"Warning: Error reading custom config file: {e}")
    
    # Load local config if it exists
    local_config_path = Path(".fwrap.toml")
    if local_config_path.exists():
        try:
            with open(local_config_path, "r") as f:
                config.update(toml.load(f))
        except (toml.TomlDecodeError, OSError) as e:
            print(f"Warning: Error reading local config file: {e}")
    
    # Apply CLI options (highest precedence)
    if cli_options:
        _deep_update(config, _expand_dotted_keys(cli_options))
    
    # Ensure required config sections exist
    if "interfaces" not in config:
        config["interfaces"] = {}
    if "local_path" not in config["interfaces"]:
        config["interfaces"]["local_path"] = "interfaces"
    if "safe" not in config:
        config["safe"] = {}
    if "rpc" not in config:
        config["rpc"] = {}
    
    # Ensure required directories exist
    Path(config["interfaces"]["local_path"]).mkdir(parents=True, exist_ok=True)
    Path(config["interfaces"]["global_path"]).mkdir(parents=True, exist_ok=True)
    Path(config["cache"]["path"]).parent.mkdir(parents=True, exist_ok=True)
    
    return config

def _expand_dotted_keys(options: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expand dotted keys in a dictionary to nested dictionaries.
    Example: {"a.b.c": "value"} -> {"a": {"b": {"c": "value"}}}
    """
    result = {}
    for key, value in options.items():
        if "." in key:
            parts = key.split(".")
            current = result
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = value
        else:
            result[key] = value
    return result

def _deep_update(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """
    Recursively update a dictionary with another dictionary.
    """
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_update(target[key], value)
        else:
            target[key] = value

def deep_merge(base: dict, override: dict) -> None:
    """Recursively merge override dict into base dict."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value

def create_default_global_config() -> None:
    """Create a default global config file if it doesn't exist."""
    if not GLOBAL_CONFIG_PATH.exists():
        with open(GLOBAL_CONFIG_PATH, "w") as f:
            toml.dump(DEFAULT_CONFIG, f)
        print(f"Created default global config at {GLOBAL_CONFIG_PATH}") 