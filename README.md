# safesmith

A Python wrapper for Foundry's forge scripts with dynamic interface generation and Safe transaction support.

## Features

- Create, sign, and queue Safe multisig transactions using Foundry scripts
- Dynamic interface generation for Foundry scripts
- Cache and manage interfaces for reuse

## Installation & Usage

### Using with UV (Recommended)

[Astral's uv](https://github.com/astral-sh/uv) is the most convenient way to install and run safesmith. You're up and running immediately with the following commands:

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Run safesmith commands directly
uvx safesmith --help
```

This creates a temporary isolated environment with all dependencies installed.

### Creating a Convenient Alias

You can create an alias for `uvx safesmith` to make it easier to call in your shell. These docs will assume an alias of `ss` has been set. For example:

```bash
ss --help
```

### Configuration

## Commands

safesmith provides several commands for working with scripts and interfaces:

- `ss run SCRIPT`: Run a Foundry script with dynamic interface handling (alias for `safe`)
- `ss list`: List all cached interfaces
- `ss clear-cache`: Clear the global interface cache
- `ss config`: Display and edit configuration
- `ss delete NONCE`: Delete a pending Safe transaction by nonce
- `ss process-interfaces SCRIPT`: Process interface directives in a script
- `ss sync-presets`: Synchronize interface presets with latest from user directory

### Common Usage Examples

```bash
# Post Safe transaction
ss run script/MyScript.s.sol --post

# Post Safe transaction with custom nonce
ss run script/MyScript.s.sol --nonce 42 --post

# Delete transaction with nonce 42 from Safe queue
ss delete 42

# List all cached interfaces
ss list

# Update presets when you've added new interface files
ss sync-presets
```

## Configuration

To generate a default local configuration for a project, run the config command at your project root:

```bash
ss config
```

Populate the .toml configuration with your project's Safe information, including the Safe address, your personal address with proposer permissions, and the alias it can be loaded from from your [cast wallet](https://book.getfoundry.sh/reference/cast/cast-wallet).

```toml
# /path/to/project/safesmith.toml
[foundry]
interfaces_path = "~/.safesmith/interfaces"
rpc_url = "https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY"

[safe]
safe_address = "0x1234...5678"
safe_proposer = "0xabcd...ef01"
proposer_alias = "my_alias_in_cast_wallet"
chain_id = 1
```

You can also use the `config set` command to create or update values within your project-level configuration.

```bash
# Set project-level configuration values
ss config set safe.safe_address 0x1234...5678
ss config set rpc.url https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY
```

### Configuration Precedence

safesmith uses a multi-level configuration system with the following priority (highest to lowest):

1. Command-line arguments
2. Environment variables (prefixed with `SAFESMITH_`)
3. Project-local config file (`./safesmith.toml`)
4. Global config file (`~/.safesmith/config.toml`)

### Global Configuration

The global configuration is created automatically on first run and stored at `~/.safesmith.config.toml`.

You can also edit the global config file directly.

### Environment Variables

All settings can be overridden with environment variables prefixed with `SAFESMITH_`, for example:

```bash
# Nested settings use double underscores between section and key
export SAFESMITH_WRAP_RPC__URL="https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY"
export SAFESMITH_WRAP_SAFE__SAFE_ADDRESS="0x1234...5678"
export SAFESMITH_WRAP_SAFE__PROPOSER="0xabcd...ef01"
```

## Interface Directives

safesmith introduces "Interface Directives" to automatically fetch and generate correct Solidity interfaces for your scripts. This feature streamlines interaction with external contracts without manual interface work.

### How Interface Directives Work

Interface directives are special annotations in your Solidity script that safesmith processes before compilation. When safesmith encounters these directives, it:

1. Identifies all `@InterfaceName` directives in your script
2. Generates or retrieves the appropriate interface files
3. Injects imports for these interfaces into your script
4. Replaces the directive syntax with the actual interface names

### Types of Interface Directives

safesmith supports two types of interface directives:

#### 1. Address-Based Directives

The format is `@InterfaceName(0xAddress)` where:

- `InterfaceName` is your chosen name for the interface (must start with a capital letter)
- `0xAddress` is the contract address to fetch the ABI from

Example:

```solidity
// This directive tells safesmith to generate an interface for the DAI token
contract MyScript is Script {
    @DAI public daiToken = @DAI(0x6B175474E89094C44Da98b954EedeAC495271d0F);

    function run() public {
        // After processing, daiToken will be a valid interface to the DAI contract
        daiToken.transfer(recipient, amount);
    }
}
```

#### 2. Preset Directives

The format is simply `@InterfaceName` (without an address) where:

- `InterfaceName` matches a known preset interface

Example:

```solidity
// This directive uses the built-in IERC20 preset
contract MyScript is Script {
    @IERC20 public token = @IERC20(tokenAddress);

    function run() external {
        // token will use the standard IERC20 interface
        token.approve(spender, amount);
    }
}
```

#### Valid Directive Format

A valid directive must follow these rules:

- Must start with `@` followed by a single capital letter (e.g., `@IERC20`)
- Must be within contract body code, not in comments
- For address-based directives, must be associated with a valid Ethereum address (0x...)

### Built-in Presets

safesmith comes with several common interface presets. The advantage of using presets is primarily to allow the developer to use directives on code lines which do not also contain an address literal (e.g. `uint256 balance = @IERC20(token).balanceOf(address(this));`)

The following presets come as part of any safesmith install:

- `IERC20` - Standard ERC20 token interface
- `IWETH` - Wrapped ETH interface
- `IERC721` - Standard NFT interface
- `IERC1155` - Multi-token standard interface
- `IUniswapV2Pair` - Uniswap V2 pair interface
- `IUniswapV2Router` - Uniswap V2 router interface
- `IUniswapV3Pool` - Uniswap V3 pool interface
- `ISafe` - Gnosis Safe interface

You can use these presets without having to specify an address on the same line:

```solidity
// Multiple presets in one script
contract MultiTokenScript is Script {
    @IERC20 public token = @IERC20(tokenAddress);
    @IWETH public weth = @IWETH(wethAddress);
    @IERC721 public nft = @IERC721(nftAddress);

    function run() external {
        // All interfaces are properly typed
        token.transfer(recipient, amount);
        weth.deposit{value: 1 ether}();
        nft.transferFrom(owner, recipient, tokenId);
    }
}
```

### Custom Presets

You can create your own preset interfaces in `~/.safesmith/presets/` directory. Any `.sol` file added there will be available as a preset.

Remember to run `ss sync-presets` after adding new preset files to update the preset index.

### Resulting File Structure

When you run a script with interface directives, safesmith creates:

1. `interfaces/` directory in your project folder
2. Interface files for each directive (e.g., `interfaces/IERC20.sol`)
3. Updates your script with appropriate imports

Your script is modified from:

```solidity
contract MyScript is Script {
    @IERC20 public token = @IERC20(0x6B175474E89094C44Da98b954EedeAC495271d0F);
    // ...
}
```

To:

```solidity
import {IERC20} from "interfaces/IERC20.sol";

contract MyScript is Script {
    IERC20 public token = IERC20(0x6B175474E89094C44Da98b954EedeAC495271d0F);
    // ...
}
```

### Interface Generation and Fallbacks

safesmith employs a multi-step process to generate interfaces:

1. First, it checks if the interface is a known preset
2. If not, it attempts to generate the interface using Foundry's `cast interface` command
3. If that fails, it tries to download the ABI from Etherscan (requires `ETHERSCAN_API_KEY` for complete results)
4. As a last resort, it creates a basic default interface with common ERC20 functions

### Interface Caching

Generated interfaces are cached for improved performance:

- **Local cache**: `./interfaces/` in your project folder
- **Global cache**: `~/.safesmith/interfaces/` for reuse across projects

You can manage the cache with these commands:

- `ss list` - List all cached interfaces
- `ss clear-cache` - Clear the global interface cache

### Complete Example

Here's a comprehensive example showing various interface directives in action:

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.13;

import "forge-std/Script.sol";

contract SafeTransaction is Script {
    // Using preset interfaces (no address needed)
    @IERC20 public token;
    @IWETH public weth;

    // Using address-based interfaces
    @UniswapRouter public router = @UniswapRouter(0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D);
    @DAI public dai = @DAI(0x6B175474E89094C44Da98b954EedeAC495271d0F);

    function run() public {
        // Load addresses from .env
        address tokenAddress = vm.envAddress("TOKEN_ADDRESS");
        address wethAddress = vm.envAddress("WETH_ADDRESS");
        address recipient = vm.envAddress("RECIPIENT");
        uint256 amount = vm.envUint("AMOUNT");

        // Initialize interfaces with addresses
        token = @IERC20(tokenAddress);
        weth = @IWETH(wethAddress);

        // Broadcast transactions
        vm.startBroadcast();

        // Use the interfaces
        token.approve(address(router), amount);

        address[] memory path = new address[](2);
        path[0] = address(token);
        path[1] = address(weth);

        router.swapExactTokensForETH(
            amount,
            0,
            path,
            recipient,
            block.timestamp + 15 minutes
        );

        vm.stopBroadcast();
    }
}
```

When executed with safesmith:

```bash
ss run script/SafeTransaction.s.sol
```

safesmith will:

1. Detect and process all four interface directives
2. Generate/retrieve the necessary interface files
3. Update the script with proper imports
4. Replace all directive instances with actual interface names

## Requirements

- Python 3.8+
- For Safe features: web3, safe-eth-py, and other Ethereum-related packages

## License

[MIT License](LICENSE)
