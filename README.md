# foundry-wrap

A Python wrapper for Foundry's forge scripts with dynamic interface generation and Safe transaction support.

## Features

- Dynamic interface generation for Foundry scripts
- Create and sign Safe transactions from Foundry scripts
- Cache and manage interfaces for reuse

## Installation & Usage

### Using with UV (Recommended)

Using foundry-wrap with [Astral's uv](https://github.com/astral-sh/uv) is the most convenient way to run it:

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Run foundry-wrap commands directly
uvx foundry-wrap --help
uvx foundry-wrap safe path/to/Script.s.sol
```

This creates a temporary isolated environment with all dependencies installed.

## Commands

- `uvx foundry-wrap run SCRIPT`: Process a script and handle interface generation
- `uvx foundry-wrap safe SCRIPT`: Process a script and create/submit a Safe transaction
- `uvx foundry-wrap list`: List all cached interfaces
- `uvx foundry-wrap clear-cache`: Clear the interface cache
- `uvx foundry-wrap config`: Display and edit configuration

## Configuration

foundry-wrap uses a multi-level configuration system with the following priority (highest to lowest):

1. Command-line arguments
2. Environment variables (prefixed with `FOUNDRY_WRAP_`)
3. Project-local config file (`./foundry-wrap.toml`)
4. Global config file (`~/.foundry-wrap/config.toml`)

### Global Configuration

The global configuration is created automatically on first run and stored at `~/.foundry-wrap/config.toml`. You can view your current global configuration:

```bash
uvx foundry-wrap config
```

You can also edit the global config file directly:

```toml
# ~/.foundry-wrap/config.toml
[foundry]
interfaces_path = "~/.foundry-wrap/interfaces"
rpc_url = "https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY"

[safe]
safe_address = "0x1234...5678"
safe_proposer = "0xabcd...ef01"
chain_id = 1
```

### Project-Local Configuration

For project-specific settings, you can use the `config set` command to create or update a project-level configuration:

```bash
# Set project-level configuration values
uvx foundry-wrap config set safe.safe_address 0x1234...5678
uvx foundry-wrap config set safe.chain_id 1
uvx foundry-wrap config set foundry.rpc_url https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY
```

This will create or update a `foundry-wrap.toml` file in your current directory:

```toml
# foundry-wrap.toml
[foundry]
rpc_url = "https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY"

[safe]
safe_address = "0x1234...5678"
chain_id = 1
```

### Common Settings

Most projects will want to configure these settings:

- `safe.safe_address`: The address of your Safe
- `safe.safe_proposer`: Your EOA address that will propose transactions
- `safe.chain_id`: The chain ID (1 for Mainnet, 5 for Goerli, etc.)
- `foundry.rpc_url`: The RPC endpoint for Ethereum interactions
- `foundry.interfaces_path`: Path to store cached interfaces

### Environment Variables

All settings can be overridden with environment variables prefixed with `FOUNDRY_WRAP_`, for example:

```bash
export FOUNDRY_WRAP_RPC_URL="https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY"
export FOUNDRY_WRAP_SAFE_ADDRESS="0x1234...5678"
```

## Requirements

- Python 3.8+
- For Safe features: web3, safe-eth-py, and other Ethereum-related packages

## License

[MIT License](LICENSE)
