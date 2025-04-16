"""Tests for proxy contract detection and ABI merging."""

import json
import os
from pathlib import Path
import pytest
from web3 import Web3
from safesmith.interface_manager import (
    InterfaceManager,
    get_implementation_address,
    merge_abis
)
from safesmith.settings import SafesmithSettings
import requests

# Known Yearn strategy proxy address
YEARN_STRATEGY_PROXY_ADDRESS = "0xC08d81aba10f2dcBA50F9A3Efbc0988439223978"

# Get environment variables or use defaults
RPC_URL = os.getenv("RPC_URL", "https://eth.llamarpc.com")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")

def get_abi(address):
    """Helper function to get ABI from Etherscan"""
    url = f"https://api.etherscan.io/api?module=contract&action=getabi&address={address}&apikey={ETHERSCAN_API_KEY}"
    response = requests.get(url)
    assert response.status_code == 200, "Failed to get ABI from Etherscan"
    data = response.json()
    assert data["status"] == "1" and data["message"] == "OK", f"Etherscan API error: {data['message']}"
    return json.loads(data["result"])

def test_proxy_detection():
    """Test that we can detect a proxy implementation."""
    web3 = Web3(Web3.HTTPProvider(RPC_URL))

    # Print storage slots for debugging
    print("\nChecking storage slots:")

    # EIP1967 implementation slot
    impl_slot = Web3.keccak(text="eip1967.proxy.implementation").hex()
    impl_value = web3.eth.get_storage_at(YEARN_STRATEGY_PROXY_ADDRESS, int(impl_slot, 16)).hex()
    print(f"EIP1967 implementation slot {impl_slot}: {impl_value}")

    # EIP1967 implementation slot minus 1
    impl_slot_minus_1 = hex(int(impl_slot, 16) - 1)
    impl_value_minus_1 = web3.eth.get_storage_at(YEARN_STRATEGY_PROXY_ADDRESS, int(impl_slot_minus_1, 16)).hex()
    print(f"EIP1967 implementation slot minus 1 {impl_slot_minus_1}: {impl_value_minus_1}")

    # EIP1967 beacon slot
    beacon_slot = Web3.keccak(text="eip1967.proxy.beacon").hex()
    beacon_value = web3.eth.get_storage_at(YEARN_STRATEGY_PROXY_ADDRESS, int(beacon_slot, 16)).hex()
    print(f"EIP1967 beacon slot {beacon_slot}: {beacon_value}")

    # EIP1822 slot
    proxiable_slot = Web3.keccak(text="PROXIABLE").hex()
    proxiable_value = web3.eth.get_storage_at(YEARN_STRATEGY_PROXY_ADDRESS, int(proxiable_slot, 16)).hex()
    print(f"EIP1822 slot {proxiable_slot}: {proxiable_value}")

    # Get implementation address
    implementation = get_implementation_address(web3, YEARN_STRATEGY_PROXY_ADDRESS)
    assert implementation is not None, "Failed to detect proxy implementation"
    print(f"\nDetected implementation address: {implementation}")

    # Get ABI from Etherscan
    abi = get_abi(implementation)
    assert abi is not None, "Failed to parse ABI"

    # Check for Yearn strategy specific functions
    function_names = [f["name"] for f in abi if f["type"] == "function"]
    assert "decimals" in function_names, "Missing decimals() function"
    assert "apiVersion" in function_names, "Missing apiVersion() function"

def test_proxy_abi_merging():
    """Test that we can merge proxy and implementation ABIs."""
    web3 = Web3(Web3.HTTPProvider(RPC_URL))
    
    # Get implementation address
    implementation = get_implementation_address(web3, YEARN_STRATEGY_PROXY_ADDRESS)
    assert implementation is not None, "Failed to detect proxy implementation"
    
    # Get ABIs
    proxy_abi = get_abi(YEARN_STRATEGY_PROXY_ADDRESS)
    impl_abi = get_abi(implementation)
    
    # Merge ABIs
    merged_abi = merge_abis(proxy_abi, impl_abi)
    
    # Check that we have both proxy and implementation functions
    function_names = [f["name"] for f in merged_abi if f["type"] == "function"]
    assert "decimals" in function_names, "Missing decimals() function in merged ABI"
    assert "apiVersion" in function_names, "Missing apiVersion() function in merged ABI"

def test_abi_merging():
    """Test ABI merging functionality."""
    # Sample proxy ABI
    proxy_abi = [
        {
            "type": "function",
            "name": "implementation",
            "inputs": [],
            "outputs": [{"type": "address"}],
            "stateMutability": "view"
        },
        {
            "type": "function",
            "name": "upgradeTo",
            "inputs": [{"type": "address", "name": "newImplementation"}],
            "outputs": [],
            "stateMutability": "nonpayable"
        }
    ]
    
    # Sample implementation ABI
    impl_abi = [
        {
            "type": "function",
            "name": "decimals",
            "inputs": [],
            "outputs": [{"type": "uint256"}],
            "stateMutability": "view"
        },
        {
            "type": "function",
            "name": "apiVersion",
            "inputs": [],
            "outputs": [{"type": "string"}],
            "stateMutability": "view"
        },
        {
            "type": "function",
            "name": "implementation",  # Duplicate function
            "inputs": [],
            "outputs": [{"type": "address"}],
            "stateMutability": "view"
        }
    ]
    
    # Merge ABIs
    merged_abi = merge_abis(proxy_abi, impl_abi)
    
    # Verify all functions are present
    function_names = [item["name"] for item in merged_abi if item.get("type") == "function"]
    assert "implementation" in function_names
    assert "upgradeTo" in function_names
    assert "decimals" in function_names
    assert "apiVersion" in function_names
    
    # Verify no duplicates
    assert len([name for name in function_names if name == "implementation"]) == 1 