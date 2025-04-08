# safesmith

A Python wrapper for Foundry's forge scripts with dynamic interface generation and Safe transaction support.

## Features

- Dynamic interface generation for Foundry scripts
- Create and sign Safe transactions from Foundry scripts
- Cache and manage interfaces for reuse

## Installation & Usage

### Using with UV (Recommended)

Using safesmith with [Astral's uv](https://github.com/astral-sh/uv) is the most convenient way to run it:

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Run safesmith commands directly
uvx safesmith --help
uvx safesmith safe path/to/Script.s.sol
```

This creates a temporary isolated environment with all dependencies installed.

## Commands

- `uvx safesmith run SCRIPT`: Process a script and handle interface generation
- `uvx safesmith safe SCRIPT`: Process a script and create/submit a Safe transaction
- `uvx safesmith list`: List all cached interfaces
- `uvx safesmith clear-cache`: Clear the interface cache
- `uvx safesmith config`: Display and edit configuration

## Configuration

safesmith uses a multi-level configuration system with the following priority (highest to lowest):

1. Command-line arguments
2. Environment variables (prefixed with `SAFESMITH_`)
3. Project-local config file (`./safesmith.toml`)
4. Global config file (`~/.safesmith/config.toml`)

### Global Configuration

The global configuration is created automatically on first run and stored at `~/.safesmith.config.toml`. You can view your current global configuration:

```bash
uvx safesmith config
```

You can also edit the global config file directly:

```toml
# ~/.safesmith/config.toml
[foundry]
interfaces_path = "~/.safesmith/interfaces"
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
uvx safesmith config set safe.safe_address 0x1234...5678
uvx safesmith config set rpc.url https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY
```

This will create or update a `safesmith.toml` file in your current directory:

```toml
# safesmith.toml
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

All settings can be overridden with environment variables prefixed with `SAFESMITH_`, for example:

```bash
# Nested settings use double underscores between section and key
export SAFESMITH_WRAP_RPC__URL="https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY"
export SAFESMITH_WRAP_SAFE__SAFE_ADDRESS="0x1234...5678"
export SAFESMITH_WRAP_SAFE__PROPOSER="0xabcd...ef01"
```

#### Environment Variable Naming

Environment variables follow this pattern:

- Prefix: `SAFESMITH_`
- Format for nested settings: `SAFESMITH_SECTION__KEY`
- Example: `safe.proposer` becomes `SAFESMITH_SAFE__PROPOSER`

Common environment variables:

- `SAFESMITH_RPC__URL` - RPC endpoint
- `SAFESMITH_SAFE__SAFE_ADDRESS` - Safe address
- `SAFESMITH_SAFE__PROPOSER` - EOA proposer address
- `ETHERSCAN_API_KEY` - Special case with no prefix

#### Settings Precedence (highest to lowest)

1. **Command-line arguments** (`--safe-address`, `--rpc-url`, etc.)
2. **Environment variables** (with `SAFESMITH_` prefix)
3. **Project-local config** (`safesmith.toml` in project directory)
4. **Global config** (`~/.safesmith/config.toml`)
5. **Default values** (defined in the code)

When multiple sources define the same setting, the higher precedence value is used.

## Interface Directives

During the course of multisig operations, it is common to want to interface with external contracts. safesmith introduces a handy feature called "Interface Directives" which, when used, will automatically fetch and generate correct interfaces for your script and seamlessly inject them into your script. Just follow these steps:

1. Add a directive in your Solidity script using the format `@InterfaceName(0x123)` followed by a contract address, where `InterfaeName` is whatever you'd like to call this interface.
2. safesmith detects these directives and fetches the address's abi from Etherscan, and uses it to generate a valid solidity interface.
3. The generated interfaces are saved to your project and imported into your script before compile time
4. The original directives are replaced with the actual interface names in the script

### Valid Directive Format

A valid directive must follow these rules:

- Must start with `@` followed by a single capital letter (e.g., `@IERC20`)
- Must be within contract body code, not in comments
- Must be associated with a valid Ethereum address (0x...)

Example of a valid directive:

```solidity
contract MyScript is Script {
    @IERC20 public token = @IERC20(0x6B175474E89094C44Da98b954EedeAC495271d0F);

    function run() public {
        // token is now a valid IERC20 interface
        token.transfer(recipient, amount);
    }
}
```

### Interface Caching

Generated interfaces are cached locally to improve performance for subsequent runs. You can:

- List cached interfaces with `uvx safesmith list`
- Clear the cache with `uvx safesmith clear-cache`

## Requirements

- Python 3.8+
- For Safe features: web3, safe-eth-py, and other Ethereum-related packages

## License

[MIT License](LICENSE)
