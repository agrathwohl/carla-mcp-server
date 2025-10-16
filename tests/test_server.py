#!/usr/bin/env python3
"""
Test script for Carla MCP Server
"""

import asyncio
import json
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from carla_controller import CarlaController


async def test_basic_operations():
    """Test basic Carla operations"""
    
    print("Carla MCP Server Test Suite")
    print("=" * 50)
    
    # Initialize Carla controller
    print("\n1. Initializing Carla controller...")
    try:
        carla = CarlaController("/home/gwohl/builds/Carla")
        print("✓ Carla controller initialized")
    except Exception as e:
        print(f"✗ Failed to initialize: {e}")
        return
    
    # Start engine
    print("\n2. Starting audio engine...")
    try:
        if carla.start_engine("JACK", "CarlaTest"):
            print("✓ Engine started successfully")
            print(f"  Sample rate: {carla.host.get_sample_rate()} Hz")
            print(f"  Buffer size: {carla.host.get_buffer_size()} samples")
        else:
            print("✗ Failed to start engine")
            # Try dummy driver as fallback
            print("  Trying dummy driver...")
            if carla.start_engine("Dummy", "CarlaTest"):
                print("✓ Engine started with dummy driver")
            else:
                print("✗ Failed with dummy driver too")
                return
    except Exception as e:
        print(f"✗ Error starting engine: {e}")
        return
    
    # Load a plugin (if available)
    print("\n3. Testing plugin loading...")
    test_plugins = [
        ("/usr/lib/lv2/calf.lv2", "http://calf.sourceforge.net/plugins/Reverb", "LV2"),
        ("/usr/lib/ladspa/cmt.so", None, "LADSPA"),
    ]
    
    loaded_plugin = None
    for path, uri, plugin_type in test_plugins:
        if os.path.exists(path):
            try:
                from carla_controller import PluginType
                ptype = getattr(PluginType, plugin_type)
                
                if plugin_type == "LV2" and uri:
                    plugin_id = carla.load_plugin(uri, ptype)
                else:
                    plugin_id = carla.load_plugin(path, ptype)
                
                if plugin_id is not None:
                    print(f"✓ Loaded {plugin_type} plugin: {carla.plugins[plugin_id]['name']}")
                    loaded_plugin = plugin_id
                    break
            except Exception as e:
                print(f"  Could not load {path}: {e}")
    
    if loaded_plugin is None:
        print("✗ No plugins could be loaded (this is OK if no plugins are installed)")
    else:
        # Test parameter operations
        print("\n4. Testing parameter control...")
        try:
            params = carla.list_parameters(loaded_plugin)
            print(f"✓ Plugin has {len(params)} parameters")
            
            if params:
                # Set first parameter
                carla.set_parameter(loaded_plugin, 0, 0.5)
                value = carla.get_parameter(loaded_plugin, 0)
                print(f"✓ Set parameter 0 to 0.5, read back: {value:.3f}")
        except Exception as e:
            print(f"✗ Parameter control error: {e}")
        
        # Test audio peaks
        print("\n5. Testing audio monitoring...")
        try:
            peaks = carla.get_audio_peaks(loaded_plugin)
            print(f"✓ Audio peaks: In L:{peaks['in_left']:.3f} R:{peaks['in_right']:.3f}, "
                  f"Out L:{peaks['out_left']:.3f} R:{peaks['out_right']:.3f}")
        except Exception as e:
            print(f"✗ Audio monitoring error: {e}")
    
    # Test session save/load
    print("\n6. Testing session management...")
    try:
        test_file = "/tmp/carla_test.carxp"
        if carla.save_project(test_file):
            print(f"✓ Saved project to {test_file}")
            
            # Try to load it back
            if carla.load_project(test_file):
                print(f"✓ Loaded project from {test_file}")
            else:
                print("✗ Failed to load project")
            
            # Clean up
            os.remove(test_file)
        else:
            print("✗ Failed to save project")
    except Exception as e:
        print(f"✗ Session management error: {e}")
    
    # Get system info
    print("\n7. System information:")
    try:
        info = carla.get_system_info()
        print(f"  Engine running: {info['engine_running']}")
        print(f"  Plugin count: {info['plugin_count']}")
        print(f"  CPU load: {info['cpu_load']:.1f}%")
    except Exception as e:
        print(f"✗ Could not get system info: {e}")
    
    # Stop engine
    print("\n8. Stopping engine...")
    carla.stop_engine()
    print("✓ Engine stopped")
    
    print("\n" + "=" * 50)
    print("Test complete!")


async def test_mcp_server():
    """Test MCP server functionality"""
    
    print("\nTesting MCP Server Integration")
    print("=" * 50)
    
    # Import server
    try:
        from server import CarlaMCPServer
        print("✓ MCP server module imported")
    except ImportError as e:
        print(f"✗ Could not import MCP server: {e}")
        print("  Make sure 'mcp' package is installed: pip install mcp")
        return
    
    # Create server instance
    try:
        server = CarlaMCPServer()
        print("✓ MCP server instance created")
    except Exception as e:
        print(f"✗ Failed to create server: {e}")
        return
    
    # Test tool execution
    print("\nTesting tool execution:")
    
    # List audio devices
    try:
        result = await server.hardware_tools.list_audio_devices()
        if result['success']:
            print(f"✓ Listed {result['total']} audio devices")
            for device in result['devices'][:3]:
                print(f"  - {device['driver']}: {device['name']}")
    except Exception as e:
        print(f"✗ Failed to list devices: {e}")
    
    print("\nMCP server test complete!")


async def main():
    """Main test function"""
    
    # Test basic operations
    await test_basic_operations()
    
    # Test MCP server if available
    await test_mcp_server()


if __name__ == "__main__":
    asyncio.run(main())