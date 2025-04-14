pragma solidity 0.8.28;
import { Script } from "forge-std/Script.sol";

contract MyScript is Script {
    function run() external {
        address ultra = 0x35282d87011f87508D457F08252Bc5bFa52E10A0;
        @IERC20 ultraToken = @IERC20(ultra);
        ultraToken.balanceOf(address(this));
        @IWETH weth = @IWETH(0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2);
        weth.deposit();
        // Use a directive with no address - should match preset
        @IERC20 token;
        // Use assertEq with a preset directive
        assertEq(@IERC20(ultra).balanceOf(address(this)), 0);
    }
} 