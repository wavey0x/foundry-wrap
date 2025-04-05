"""Configuration management for foundry-wrap"""

import os
from pathlib import Path
from typing import Dict, Any, Optional

from foundry_wrap.settings import (
    GLOBAL_CONFIG_PATH, 
    FOUNDRY_WRAP_DIR, 
    create_default_global_config,
    load_settings,
    FoundryWrapSettings
)

# Load environment variables
load_dotenv()

# Get user's home directory
HOME_DIR = Path.home()
FOUNDRY_WRAP_DIR = HOME_DIR / ".foundry-wrap"
FOUNDRY_WRAP_DIR.mkdir(exist_ok=True)

# Global config path
GLOBAL_CONFIG_PATH = HOME_DIR / ".foundry-wrap" / "config.toml"

# Default configuration
DEFAULT_CONFIG = {
    "cache": {
        "path": str(FOUNDRY_WRAP_DIR / "interface-cache.json"),
        "enabled": True,
    },
    "interfaces": {
        "global_path": str(FOUNDRY_WRAP_DIR / "interfaces"),  # Global interfaces
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
    Load configuration from various sources in order of precedence.
    This is maintained for backward compatibility.
    
    Returns a dictionary with the settings.
    """
    settings = load_settings(config_path, cli_options)
    # Convert the Pydantic Settings to a dictionary for backward compatibility
    return settings.model_dump(mode="python")

def _expand_dotted_keys(options: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expand dotted keys in a dictionary to nested dictionaries.
    Example: {"a.b.c": "value"} -> {"a": {"b": {"c": "value"}}}
    
    Maintained for backward compatibility.
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
    
    Maintained for backward compatibility.
    """
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_update(target[key], value)
        else:
            target[key] = value

def deep_merge(base: dict, override: dict) -> None:
    """
    Recursively merge override dict into base dict.
    
    Maintained for backward compatibility.
    """
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