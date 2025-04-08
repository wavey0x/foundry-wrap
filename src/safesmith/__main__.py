"""Entry point for directly executing the package."""

from safesmith.cli import main
from safesmith.settings import GLOBAL_CONFIG_PATH, create_default_global_config

# Ensure global config exists before running
if not GLOBAL_CONFIG_PATH.exists():
    create_default_global_config()

if __name__ == "__main__":
    main() 