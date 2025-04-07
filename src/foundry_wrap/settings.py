"""Settings management for foundry-wrap using Pydantic Settings."""

import os
from pathlib import Path
from typing import Dict, Any, Optional, List, Set

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, PydanticBaseSettingsSource
from dotenv import load_dotenv
import toml

# Load environment variables
load_dotenv()

# Get user's home directory - ensure it's a Path object
HOME_DIR = Path.home()
FOUNDRY_WRAP_DIR = HOME_DIR / ".foundry-wrap"
FOUNDRY_WRAP_DIR.mkdir(exist_ok=True)

# Global config path
GLOBAL_CONFIG_PATH = FOUNDRY_WRAP_DIR / "config.toml"


class CacheSettings(BaseSettings):
    """Cache settings."""
    path: str = str(FOUNDRY_WRAP_DIR / "interface-cache.json")
    enabled: bool = True


class InterfacesSettings(BaseSettings):
    """Interface-related settings."""
    global_path: str = str(FOUNDRY_WRAP_DIR / "interfaces")
    local_path: str = "interfaces"
    overwrite: bool = False


class EtherscanSettings(BaseSettings):
    """Etherscan API settings."""
    api_key: str = Field(default="", env="ETHERSCAN_API_KEY")


class SafeSettings(BaseSettings):
    """Gnosis Safe related settings."""
    safe_address: str = ""
    proposer: str = ""
    proposer_alias: str = ""

class RpcSettings(BaseSettings):
    """RPC-related settings."""
    url: str = "https://eth.merkle.io"


class TomlConfigSettingsSource(PydanticBaseSettingsSource):
    """
    A settings source that loads from a TOML file.
    """

    def __init__(self, settings_cls: type[BaseSettings], config_path: Optional[Path] = None):
        super().__init__(settings_cls)
        self.config_path = config_path

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        if self.config_path and self.config_path.exists():
            try:
                config_data = toml.load(self.config_path)
                
                # Try to find the field in the TOML data
                for section in config_data:
                    if field_name in config_data[section]:
                        return config_data[section][field_name], field_name, False
                    
                    # Handle nested sections matching our settings classes
                    if section == field_name and isinstance(config_data[section], dict):
                        return config_data[section], field_name, True
            except Exception:
                pass
        
        return None, field_name, False

    def __call__(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        
        if not self.config_path or not self.config_path.exists():
            return d
            
        try:
            # Process the loaded data
            for field_name, field in self.settings_cls.model_fields.items():
                field_value, field_key, value_is_complex = self.get_field_value(field, field_name)
                if field_value is not None:
                    d[field_key] = field_value
                    
        except Exception as e:
            print(f"Warning: Error loading TOML file {self.config_path}: {e}")
            
        return d


class FoundryWrapSettings(BaseSettings):
    """Main settings class for foundry-wrap."""
    
    cache: CacheSettings = Field(default_factory=CacheSettings)
    interfaces: InterfacesSettings = Field(default_factory=InterfacesSettings)
    etherscan: EtherscanSettings = Field(default_factory=EtherscanSettings)
    safe: SafeSettings = Field(default_factory=SafeSettings)
    rpc: RpcSettings = Field(default_factory=RpcSettings)
    
    model_config = SettingsConfigDict(env_prefix="FOUNDRY_WRAP_", env_nested_delimiter="__")
    
    @model_validator(mode="after")
    def ensure_directories_exist(self) -> "FoundryWrapSettings":
        """Ensure all required directories exist."""
        try:
            # Use Path objects for more reliable directory creation
            Path(self.interfaces.local_path).mkdir(parents=True, exist_ok=True)
            Path(self.interfaces.global_path).mkdir(parents=True, exist_ok=True)
            
            # Fix potential PATH environment variable being used incorrectly
            cache_path = self.cache.path
            if ':' in cache_path or len(cache_path) > 255:  # Check for PATH-like string or excessive length
                # This is likely an environment variable issue
                self.cache.path = str(FOUNDRY_WRAP_DIR / "interface-cache.json")
                cache_path = self.cache.path
                print(f"Warning: Invalid cache path detected. Defaulting to {cache_path}")
                
            # Make sure the parent directory exists
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            
        except Exception as e:
            print(f"Warning: Error ensuring directories exist: {e}")
        
        return self
    
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """
        Customize settings sources with the following priority:
        1. CLI arguments (init_settings)
        2. Environment variables
        3. Local foundry-wrap.toml
        4. Global ~/.foundry-wrap/config.toml
        5. Default values
        """
        local_config = TomlConfigSettingsSource(settings_cls, Path("foundry-wrap.toml"))
        global_config = TomlConfigSettingsSource(settings_cls, GLOBAL_CONFIG_PATH)
        
        return (init_settings, env_settings, dotenv_settings, local_config, global_config)


def create_default_config(config_path: Path, is_global: bool = False) -> None:
    """Create default configuration file at the specified path."""
    # Ensure the directory exists
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)
    
    # Create the interfaces directory as well
    interfaces_dir = config_dir / "interfaces"
    interfaces_dir.mkdir(exist_ok=True)
    
    # Generate config from Pydantic defaults rather than manually specifying
    settings = FoundryWrapSettings()
    config_dict = {}
    
    # Convert to hierarchical dict for TOML serialization
    model_data = settings.model_dump(mode="python")
    for key, value in model_data.items():
        if isinstance(value, dict):
            config_dict[key] = value
        else:
            if key not in config_dict:
                config_dict[key] = {}
            config_dict[key] = value
    
    # Remove specific fields for local config
    if not is_global:
        config_dict["cache"].pop("path", None)
        config_dict["interfaces"].pop("global_path", None)
    
    # Write the config file
    with open(config_path, "w") as f:
        if not is_global:
            f.write("# Global config is located at ~/.foundry-wrap/\n\n")
        toml.dump(config_dict, f)
    
    print(f"Created default config at {config_path}")


def load_settings(config_path: Optional[str] = None, cli_options: Dict[str, Any] = None) -> FoundryWrapSettings:
    """
    Load settings from various sources in order of precedence.
    """
    # Ensure the global config exists
    if not GLOBAL_CONFIG_PATH.exists():
        create_default_config(GLOBAL_CONFIG_PATH)
    
    # Load settings from the global config file
    global_config_data = {}
    if GLOBAL_CONFIG_PATH.exists():
        with open(GLOBAL_CONFIG_PATH, "r") as f:
            global_config_data = toml.load(f)
    
    # Load settings from the local project config file
    local_config_data = {}
    local_config_path = Path("foundry-wrap.toml")
    if local_config_path.exists():
        with open(local_config_path, "r") as f:
            local_config_data = toml.load(f)
    
    # Process CLI options to flatten nested mappings
    if cli_options:
        # Convert dotted keys to nested dicts first
        nested_options = {}
        for key, value in cli_options.items():
            if value is None:
                continue
            if "." in key:
                parts = key.split(".")
                current = nested_options
                for part in parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = value
            else:
                nested_options[key] = value
                
        # Now convert to Pydantic-settings format
        flattened_options = {}
        for section, values in nested_options.items():
            if isinstance(values, dict):
                for key, value in values.items():
                    flattened_options[f"{section}__{key}"] = value
            else:
                flattened_options[section] = values
    else:
        flattened_options = {}
    
    # Merge configurations with the following precedence:
    # 1. CLI options
    # 2. Local project config
    # 3. Global config
    merged_config = {**global_config_data, **local_config_data, **flattened_options}
    
    # Create settings with merged config
    return FoundryWrapSettings(**merged_config)