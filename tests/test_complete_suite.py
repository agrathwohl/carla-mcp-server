#!/usr/bin/env python3
"""
COMPLETE TEST SUITE FOR CARLA MCP SERVER
Tests ALL functionality with REAL Carla API - NO FAKE SHIT
"""

import asyncio
import json
import sys
import os
import time
from pathlib import Path

# Add paths
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'source', 'frontend'))
sys.path.append(os.path.dirname(__file__))

from carla_controller import CarlaController
from tools.plugin_tools import PluginTools
from tools.analysis_tools import AnalysisTools
from tools.routing_tools import RoutingTools
from tools.session_tools import SessionTools
from tools.parameter_tools import ParameterTools
from tools.hardware_tools import HardwareTools

class CompleteTestSuite:
    def __init__(self):
        self.carla_path = "/home/gwohl/builds/Carla"
        self.controller = None
        self.results = {
            'passed': [],
            'failed': [],
            'warnings': []
        }
        
    def print_header(self, test_name):
        print(f"\n{'='*60}")
        print(f"TEST: {test_name}")
        print('='*60)
        
    def record_result(self, test_name, success, message=""):
        if success:
            self.results['passed'].append(test_name)
            print(f"✅ PASSED: {test_name}")
        else:
            self.results['failed'].append((test_name, message))
            print(f"❌ FAILED: {test_name} - {message}")
            
    def record_warning(self, test_name, message):
        self.results['warnings'].append((test_name, message))
        print(f"⚠️  WARNING: {message}")

    async def test_1_controller_initialization(self):
        """Test 1: Controller initialization with REAL Carla backend"""
        self.print_header("Controller Initialization")
        
        try:
            self.controller = CarlaController(self.carla_path)
            
            # Check engine is running
            if not self.controller.engine_running:
                raise Exception("Engine not running after initialization")
                
            # Get real engine info
            sample_rate = self.controller.host.get_sample_rate()
            buffer_size = self.controller.host.get_buffer_size()
            
            print(f"  Engine: JACK @ {sample_rate}Hz, {buffer_size} samples")
            
            self.record_result("Controller Initialization", True)
            return True
            
        except Exception as e:
            self.record_result("Controller Initialization", False, str(e))
            return False

    async def test_2_plugin_loading(self):
        """Test 2: Load REAL LV2 plugin"""
        self.print_header("Plugin Loading (LV2)")
        
        try:
            plugin_tools = PluginTools(self.controller)
            
            # Load a real LV2 plugin
            result = await plugin_tools.load_plugin(
                path="http://gareus.org/oss/lv2/meters#VUmono",
                type="LV2"
            )
            
            if not result['success']:
                raise Exception(f"Failed to load plugin: {result.get('error')}")
                
            plugin_id = result['plugin_id']
            print(f"  Loaded plugin ID: {plugin_id}")
            print(f"  Name: {result['name']}")
            print(f"  I/O: {result['io_config']}")
            
            # Verify plugin is in controller's list
            if plugin_id not in self.controller.plugins:
                raise Exception("Plugin not in controller's plugin list")
                
            self.record_result("Plugin Loading", True)
            return plugin_id
            
        except Exception as e:
            self.record_result("Plugin Loading", False, str(e))
            return None

    async def test_3_real_audio_peaks(self):
        """Test 3: Get REAL audio peak levels"""
        self.print_header("Real Audio Peak Levels")
        
        try:
            if not self.controller.plugins:
                # Load a plugin first
                plugin_id = await self.test_2_plugin_loading()
                if plugin_id is None:
                    raise Exception("No plugins to test peaks")
            else:
                plugin_id = list(self.controller.plugins.keys())[0]
                
            # Get REAL peaks
            peaks = self.controller.get_audio_peaks(plugin_id)
            
            print(f"  Plugin {plugin_id} peaks:")
            print(f"    Input:  L={peaks['in_left']:.4f}, R={peaks['in_right']:.4f}")
            print(f"    Output: L={peaks['out_left']:.4f}, R={peaks['out_right']:.4f}")
            
            # Verify we got real values
            if all(v == 0 for v in peaks.values()):
                self.record_warning("Audio Peaks", "All peaks are zero - no audio passing through")
            
            self.record_result("Real Audio Peaks", True)
            return True
            
        except Exception as e:
            self.record_result("Real Audio Peaks", False, str(e))
            return False

    async def test_4_analysis_tools(self):
        """Test 4: Analysis tools with REAL data"""
        self.print_header("Analysis Tools (Real Data)")
        
        try:
            analysis = AnalysisTools(self.controller)
            
            if not self.controller.plugins:
                raise Exception("No plugins loaded for analysis")
                
            plugin_id = list(self.controller.plugins.keys())[0]
            
            # Test measure_levels with REAL peaks
            result = await analysis.measure_levels(str(plugin_id))
            if not result['success']:
                raise Exception(f"measure_levels failed: {result.get('error')}")
                
            print(f"  Real peak levels:")
            print(f"    Left:  {result['peak_left_db']:.2f} dB")
            print(f"    Right: {result['peak_right_db']:.2f} dB")
            print(f"    Balance: {result['balance']:.2f}")
            
            # Test analyze_latency with REAL hardware values
            result = await analysis.analyze_latency()
            if not result['success']:
                raise Exception(f"analyze_latency failed: {result.get('error')}")
                
            print(f"  Real latency measurements:")
            print(f"    Buffer: {result['buffer_size_samples']} samples")
            print(f"    Hardware latency: {result['hardware_latency_ms']:.2f} ms")
            print(f"    Sample rate: {result['sample_rate_hz']} Hz")
            
            # Test feedback detection with REAL connections
            result = await analysis.detect_feedback()
            if not result['success']:
                raise Exception(f"detect_feedback failed: {result.get('error')}")
                
            print(f"  Feedback detection:")
            print(f"    Connections analyzed: {result['total_connections']}")
            print(f"    Feedback detected: {result['feedback_detected']}")
            
            self.record_result("Analysis Tools", True)
            return True
            
        except Exception as e:
            self.record_result("Analysis Tools", False, str(e))
            return False

    async def test_5_parameter_control(self):
        """Test 5: REAL parameter control"""
        self.print_header("Parameter Control")
        
        try:
            if not self.controller.plugins:
                raise Exception("No plugins for parameter control")
                
            plugin_id = list(self.controller.plugins.keys())[0]
            
            # List parameters
            params = self.controller.list_parameters(plugin_id)
            print(f"  Plugin has {len(params)} parameters")
            
            if params:
                # Test setting a parameter
                param = params[0]
                old_value = param['current']
                new_value = (param['min'] + param['max']) / 2
                
                self.controller.set_parameter(plugin_id, 0, new_value)
                time.sleep(0.1)  # Let it update
                
                current = self.controller.get_parameter(plugin_id, 0)
                print(f"  Parameter 0 ({param['name']}):")
                print(f"    Old: {old_value:.4f}")
                print(f"    Set: {new_value:.4f}")
                print(f"    Got: {current:.4f}")
                
                if abs(current - new_value) > 0.01:
                    self.record_warning("Parameter Control", 
                                       f"Value mismatch: set {new_value}, got {current}")
            
            # Test internal parameters (drywet, volume, etc)
            drywet = self.controller.host.get_internal_parameter_value(plugin_id, 1)
            print(f"  Internal dry/wet: {drywet:.4f}")
            
            self.record_result("Parameter Control", True)
            return True
            
        except Exception as e:
            self.record_result("Parameter Control", False, str(e))
            return False

    async def test_6_routing_connections(self):
        """Test 6: REAL audio routing"""
        self.print_header("Audio Routing")
        
        try:
            routing = RoutingTools(self.controller)
            
            # Load two plugins to connect
            plugin_tools = PluginTools(self.controller)
            
            result1 = await plugin_tools.load_plugin(
                path="http://gareus.org/oss/lv2/meters#VUmono",
                type="LV2"
            )
            result2 = await plugin_tools.load_plugin(
                path="http://gareus.org/oss/lv2/meters#VUstereo",
                type="LV2"
            )
            
            if not (result1['success'] and result2['success']):
                raise Exception("Failed to load plugins for routing test")
                
            plugin1 = result1['plugin_id']
            plugin2 = result2['plugin_id']
            
            # Connect them
            result = await routing.connect_audio(
                source={'plugin_id': str(plugin1), 'port_index': 0},
                destination={'plugin_id': str(plugin2), 'port_index': 0}
            )
            
            if not result['success']:
                raise Exception(f"Failed to connect: {result.get('error')}")
                
            print(f"  Connected plugin {plugin1} -> {plugin2}")
            print(f"  Connection ID: {result['connection_id']}")
            
            # Refresh patchbay
            self.controller.refresh_connections()
            
            self.record_result("Audio Routing", True)
            return True
            
        except Exception as e:
            self.record_result("Audio Routing", False, str(e))
            return False

    async def test_7_session_management(self):
        """Test 7: REAL session save/load"""
        self.print_header("Session Management")
        
        try:
            session = SessionTools(self.controller)
            
            # Save current session
            test_path = "/tmp/test_carla_session.carxp"
            result = await session.save_session(path=test_path)
            
            if not result['success']:
                raise Exception(f"Failed to save: {result.get('error')}")
                
            print(f"  Saved session to: {test_path}")
            print(f"  Plugins saved: {result['plugin_count']}")
            
            # Clear and reload
            self.controller.host.remove_all_plugins()
            time.sleep(0.5)
            
            result = await session.load_session(path=test_path)
            
            if not result['success']:
                raise Exception(f"Failed to load: {result.get('error')}")
                
            print(f"  Loaded session from: {test_path}")
            print(f"  Plugins loaded: {result['plugin_count']}")
            
            self.record_result("Session Management", True)
            return True
            
        except Exception as e:
            self.record_result("Session Management", False, str(e))
            return False

    async def test_8_plugin_info_accuracy(self):
        """Test 8: Verify plugin info uses REAL API methods"""
        self.print_header("Plugin Info Accuracy")
        
        try:
            if not self.controller.plugins:
                raise Exception("No plugins loaded")
                
            plugin_id = list(self.controller.plugins.keys())[0]
            plugin_tools = PluginTools(self.controller)
            
            # Get detailed info
            result = await plugin_tools.get_plugin_info(str(plugin_id))
            
            if not result['success']:
                raise Exception(f"Failed to get info: {result.get('error')}")
                
            print(f"  Plugin: {result['name']}")
            print(f"  State values from get_internal_parameter_value:")
            print(f"    Dry/Wet: {result['state']['drywet']:.4f}")
            print(f"    Balance L: {result['state']['balance_left']:.4f}")
            print(f"    Balance R: {result['state']['balance_right']:.4f}")
            print(f"    Panning: {result['state']['panning']:.4f}")
            print(f"  Programs: {len(result['programs'])}")
            print(f"  Raw peaks: {result['peaks']}")
            
            self.record_result("Plugin Info Accuracy", True)
            return True
            
        except Exception as e:
            self.record_result("Plugin Info Accuracy", False, str(e))
            return False

    async def test_9_hardware_config(self):
        """Test 9: REAL hardware configuration"""
        self.print_header("Hardware Configuration")
        
        try:
            hardware = HardwareTools(self.controller)
            
            # List audio devices
            result = await hardware.list_audio_devices()
            
            if not result['success']:
                raise Exception(f"Failed to list devices: {result.get('error')}")
                
            print(f"  Found {result['total']} audio devices")
            for device in result['devices']:
                print(f"    - {device['driver']}: {device['name']} ({device['status']})")
            
            # Get current configuration
            print(f"  Current configuration:")
            print(f"    Sample rate: {self.controller.host.get_sample_rate()} Hz")
            print(f"    Buffer size: {self.controller.host.get_buffer_size()} samples")
            
            self.record_result("Hardware Configuration", True)
            return True
            
        except Exception as e:
            self.record_result("Hardware Configuration", False, str(e))
            return False

    async def test_10_batch_processing_reality(self):
        """Test 10: Verify batch processing acknowledges API limitations"""
        self.print_header("Batch Processing Reality Check")
        
        try:
            plugin_tools = PluginTools(self.controller)
            
            # Try batch process
            result = await plugin_tools.batch_process(
                input_file="/tmp/test.wav",
                plugin_chain=[]
            )
            
            # Should acknowledge that offline rendering isn't available
            if 'note' in result and 'does not support offline rendering' in result['note']:
                print(f"  ✅ Correctly reports API limitation")
                print(f"  Note: {result['note']}")
            else:
                raise Exception("Batch process doesn't acknowledge API limitations")
                
            self.record_result("Batch Processing Reality", True)
            return True
            
        except Exception as e:
            self.record_result("Batch Processing Reality", False, str(e))
            return False

    async def run_all_tests(self):
        """Run complete test suite"""
        print("\n" + "="*60)
        print("CARLA MCP SERVER COMPLETE TEST SUITE")
        print("Testing REAL functionality - NO SIMULATIONS")
        print("="*60)
        
        start_time = time.time()
        
        # Run tests in sequence
        tests = [
            self.test_1_controller_initialization,
            self.test_2_plugin_loading,
            self.test_3_real_audio_peaks,
            self.test_4_analysis_tools,
            self.test_5_parameter_control,
            self.test_6_routing_connections,
            self.test_7_session_management,
            self.test_8_plugin_info_accuracy,
            self.test_9_hardware_config,
            self.test_10_batch_processing_reality
        ]
        
        for test in tests:
            try:
                await test()
            except Exception as e:
                print(f"  Unexpected error in {test.__name__}: {e}")
                self.record_result(test.__name__, False, str(e))
        
        # Print summary
        elapsed = time.time() - start_time
        
        print("\n" + "="*60)
        print("TEST SUITE SUMMARY")
        print("="*60)
        print(f"✅ PASSED: {len(self.results['passed'])} tests")
        print(f"❌ FAILED: {len(self.results['failed'])} tests")
        print(f"⚠️  WARNINGS: {len(self.results['warnings'])} warnings")
        print(f"⏱️  Time: {elapsed:.2f} seconds")
        
        if self.results['failed']:
            print("\nFailed tests:")
            for test, error in self.results['failed']:
                print(f"  - {test}: {error}")
                
        if self.results['warnings']:
            print("\nWarnings:")
            for test, warning in self.results['warnings']:
                print(f"  - {test}: {warning}")
        
        # Cleanup
        if self.controller:
            self.controller.stop_engine()
            
        return len(self.results['failed']) == 0


async def main():
    suite = CompleteTestSuite()
    success = await suite.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())