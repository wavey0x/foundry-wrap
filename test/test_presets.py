#!/usr/bin/env python3
import unittest
from pathlib import Path
import sys
import tempfile
import shutil

# Add the project root to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.safesmith.settings import SafesmithSettings
from src.safesmith.interface_manager import InterfaceManager
from src.safesmith.script_parser import ScriptParser

class TestPresets(unittest.TestCase):
    def setUp(self):
        # Create test directories
        self.temp_dir = Path(tempfile.mkdtemp())
        self.test_interfaces_dir = self.temp_dir / "interfaces"
        self.test_presets_dir = self.temp_dir / "presets"
        self.test_interfaces_dir.mkdir(exist_ok=True)
        self.test_presets_dir.mkdir(exist_ok=True)
        
        # Create test-specific settings
        self.settings = SafesmithSettings(
            interfaces={"local_path": str(self.test_interfaces_dir), "global_path": str(self.test_interfaces_dir)},
            presets={"path": str(self.test_presets_dir), "index_file": str(self.test_presets_dir / ".index.json")}
        )
        
        # Initialize interface manager with test settings
        self.interface_manager = InterfaceManager(self.settings)
        
        # Copy preset files to test directory
        package_presets_dir = Path(__file__).parent.parent / "src" / "safesmith" / "presets"
        if package_presets_dir.exists():
            for preset_file in package_presets_dir.glob("*.sol"):
                target_path = self.test_presets_dir / preset_file.name
                shutil.copy(preset_file, target_path)
        
        # Create test script
        self.test_script = Path(__file__).parent / "test_preset.sol"
    
    def tearDown(self):
        # Clean up temporary directory
        shutil.rmtree(self.temp_dir)
    
    def test_preset_loading(self):
        """Test that presets are properly loaded"""
        # Update the index to include our test presets
        self.interface_manager.update_preset_index()
        
        # Load the presets
        presets = self.interface_manager.load_preset_index()
        self.assertGreater(len(presets), 0, "No presets found")
        self.assertIn("IERC20", presets, "IERC20 preset not found")
        
    def test_script_parsing(self):
        """Test parsing of a script with preset directives"""
        # First update the preset index
        self.interface_manager.update_preset_index()
        
        # Now parse the script
        parser = ScriptParser(self.test_script, verbose=True, interface_manager=self.interface_manager)
        interfaces = parser.parse_interfaces()
        
        # Verify IERC20 is found
        self.assertIn("IERC20", interfaces, "IERC20 interface not found in script")
        
        # Verify IWETH is found
        self.assertIn("IWETH", interfaces, "IWETH interface not found in script")
        
        # Process interfaces and verify they're created
        for name, address in interfaces.items():
            path = self.interface_manager.process_interface(name, address)
            self.assertTrue(path.exists(), f"Interface file for {name} was not created")

if __name__ == "__main__":
    unittest.main() 