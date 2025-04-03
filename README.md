# fwrap - Foundry Script Wrapper

**fwrap is a Python wrapper for Foundry forge scripts with dynamic interface generation and Safe transaction support.**

## Features

- **Dynamic Interface Generation**: Automatically generate Solidity interfaces by using special `FW-Interface` directives in your scripts
- **Safe Transaction Integration**: Create and submit Gnosis Safe multisig transactions directly from forge script outputs
- **Interface Caching**: Both local (project-specific) and global caching of interfaces for faster development
- **Etherscan Integration**: Fallback to downloading ABIs from Etherscan when necessary

## Installation

```shell
# Install from PyPI
pip install fwrap

# Or install with Safe transaction support
pip install 'fwrap[safe]'
```

## Usage

### Dynamic Interface Generation

In your Foundry script, use the `FW-ICustomName` directive to automatically generate interfaces (where the "ICustomName" portion is whatever you'd like to call it). fwrap will parse your script, and automatically fetch the interface and import it. Interfaces are fetched from etherscan for the address wrapped by the FW- directive, and are cached locally for future usage.

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.13;

import {Script} from "forge-std/Script.sol";

contract MyScript is Script {
    function run() public {
        // FW-IERC20 will be automatically replaced with "IERC20", and the contract abi will be used to generate and import a new interface file
        FW-IERC20 token = FW-IERC20(0x6B175474E89094C44Da98b954EedeAC495271d0F); // DAI
        
        vm.startBroadcast();
        uint256 balance = token.balanceOf(msg.sender);
        vm.stopBroadcast();
    }
}
```

Then run the script with fwrap:

```shell
fwrap run script/MyScript.s.sol
```

### Safe Transaction Creation

Create and propose Gnosis Safe transactions directly from your Foundry scripts:

```shell
# Configure Safe settings
fwrap config --global --safe-address YOUR_SAFE_ADDRESS --safe-proposer YOUR_ADDRESS --rpc-url YOUR_RPC_URL

# Create and propose a Safe transaction from a script
fwrap safe script/MyScript.s.sol
```

### Interface Management

```shell
# List all cached interfaces
fwrap list

# Clear the global interface cache
fwrap clear-cache
```

## Configuration

Create a global configuration:

```shell
fwrap config --global --interfaces-path ./my-interfaces --rpc-url https://mainnet.infura.io/v3/YOUR_KEY
```

Or use project-specific configuration:

```shell
fwrap config --interfaces-path ./interfaces
```

## Environment Variables

You can use these environment variables instead of configuration:

- `RPC_URL` - Default RPC URL
- `SAFE_ADDRESS` - Default Safe address
- `SAFE_PROPOSER` - Default Safe proposer address
- `ETHERSCAN_API_KEY` - API key for Etherscan interface generation
- `CHAIN_ID` - Chain ID for Safe transaction service (default: 1)

## Requirements

- Python 3.8+
- Foundry (Forge, Cast)

## How It Works

1. fwrap scans your script for `FW-Interface` directives
2. It generates interfaces using Cast or Etherscan
3. It updates your script with proper imports and interface references
4. When using the Safe features, it runs your script and batches all transactions through the MultiSend contract

## License

MIT
