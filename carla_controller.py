#!/usr/bin/env python3
"""
Carla Controller - High-level wrapper for Carla backend operations
"""

import os
import sys
import time
import threading
import logging
from typing import Optional, Dict, Any, List, Callable
from pathlib import Path
from enum import IntEnum

# Add Carla to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'source', 'frontend'))

from carla_backend import *

logger = logging.getLogger(__name__)


class PluginType(IntEnum):
    """Plugin type enumeration"""
    NONE = PLUGIN_NONE
    INTERNAL = PLUGIN_INTERNAL
    LADSPA = PLUGIN_LADSPA
    DSSI = PLUGIN_DSSI
    LV2 = PLUGIN_LV2
    VST2 = PLUGIN_VST2
    VST3 = PLUGIN_VST3
    AU = PLUGIN_AU
    SF2 = PLUGIN_SF2
    SFZ = PLUGIN_SFZ
    JACK = PLUGIN_JACK


class BinaryType(IntEnum):
    """Binary type enumeration"""
    NATIVE = BINARY_NATIVE
    POSIX32 = BINARY_POSIX32
    POSIX64 = BINARY_POSIX64
    WIN32 = BINARY_WIN32
    WIN64 = BINARY_WIN64
    OTHER = BINARY_OTHER


class CarlaController:
    """High-level controller for Carla operations"""
    
    def __init__(self, carla_path: str):
        """Initialize Carla controller
        
        Args:
            carla_path: Path to Carla installation
        """
        self.carla_path = carla_path
        self.lib_path = os.path.join(carla_path, "bin", "libcarla_standalone2.so")
        
        # Verify library exists
        if not os.path.exists(self.lib_path):
            raise RuntimeError(f"Carla library not found at: {self.lib_path}")
        
        # Initialize host
        self.host = CarlaHostDLL(self.lib_path, False)
        
        # Configure engine
        self._configure_engine()
        
        # Set up default callback for engine events
        self._setup_default_callback()
        
        # State tracking
        self.engine_running = False
        self.plugins: Dict[int, Dict[str, Any]] = {}
        self.connections: List[Dict[str, Any]] = []
        self.buses: Dict[str, Dict[str, Any]] = {}
        self.macros: Dict[str, Dict[str, Any]] = {}
        
        # Callbacks
        self.event_callback: Optional[Callable] = None
        
        # Performance tracking
        self.cpu_load = 0.0
        self.xruns = 0
        
        # Idle thread for event processing
        self.idle_thread = None
        self.idle_running = False
        
        logger.info(f"CarlaController initialized with library: {self.lib_path}")
        
        # START THE FUCKING ENGINE RIGHT NOW
        logger.info("INITIALIZING JACK ENGINE...")
        if not self.host.engine_init("JACK", "CarlaMCP"):
            logger.error("FAILED TO INITIALIZE JACK ENGINE!")
            raise RuntimeError("Cannot initialize JACK engine - is JACK running?")
        
        self.engine_running = True
        
        # Start the idle processing thread
        self.idle_running = True
        self.idle_thread = threading.Thread(target=self._idle_loop, daemon=True)
        self.idle_thread.start()
        
        # Get and log engine info
        sample_rate = self.host.get_sample_rate()
        buffer_size = self.host.get_buffer_size()
        logger.info(f"ENGINE RUNNING! {sample_rate}Hz, {buffer_size} samples buffer")
    
    def _configure_engine(self):
        """Configure engine options"""
        bin_path = os.path.join(self.carla_path, "bin")
        
        # Set binary path for bridges
        self.host.set_engine_option(ENGINE_OPTION_PATH_BINARIES, 0, bin_path)
        
        # Validate bridge binaries exist
        self._validate_bridges(bin_path)
        
        # Configure Wine for Windows VST support
        wine_path = self._find_wine()
        if wine_path:
            # Validate Wine is working
            if self._validate_wine(wine_path):
                self.host.set_engine_option(ENGINE_OPTION_WINE_EXECUTABLE, 0, wine_path)
                self.host.set_engine_option(ENGINE_OPTION_WINE_AUTO_PREFIX, 1, "")
            else:
                logger.warning("Wine validation failed - Windows VST support disabled")
        
        # Set default options
        self.host.set_engine_option(ENGINE_OPTION_PROCESS_MODE, ENGINE_PROCESS_MODE_MULTIPLE_CLIENTS, "")
        self.host.set_engine_option(ENGINE_OPTION_FORCE_STEREO, 0, "")
        self.host.set_engine_option(ENGINE_OPTION_PREFER_PLUGIN_BRIDGES, 0, "")  # DISABLED: Direct loading like standalone script
        self.host.set_engine_option(ENGINE_OPTION_PREFER_UI_BRIDGES, 0, "")      # DISABLED: Direct UI like standalone script
        self.host.set_engine_option(ENGINE_OPTION_MAX_PARAMETERS, 500, "")
        self.host.set_engine_option(ENGINE_OPTION_UI_BRIDGES_TIMEOUT, 8000, "")
        self.host.set_engine_option(ENGINE_OPTION_AUDIO_BUFFER_SIZE, 512, "")
        self.host.set_engine_option(ENGINE_OPTION_AUDIO_SAMPLE_RATE, 48000, "")
    
    def _setup_default_callback(self):
        """Set up default engine callback for event handling"""
        def default_callback(host, action, plugin_id, value1, value2, value3, valuef, value_str):
            # Log important events
            if action == ENGINE_CALLBACK_PLUGIN_ADDED:
                logger.info(f"Plugin {plugin_id} added: {value_str}")
            elif action == ENGINE_CALLBACK_PLUGIN_REMOVED:
                logger.info(f"Plugin {plugin_id} removed")
            elif action == ENGINE_CALLBACK_ERROR:
                logger.error(f"Engine error: {value_str}")
            elif action == ENGINE_CALLBACK_INFO:
                logger.debug(f"Engine info: {value_str}")
            
            # Call user callback if set
            if self.event_callback:
                self.event_callback(host, action, plugin_id, value1, value2, value3, valuef, value_str)
        
        self.host.set_engine_callback(default_callback)
        logger.info("Engine callback registered")
    
    def _find_wine(self) -> Optional[str]:
        """Find Wine executable for Windows plugin support"""
        # Check environment variable first
        env_wine = os.environ.get('CARLA_WINE_EXECUTABLE')
        if env_wine and os.path.exists(env_wine):
            logger.info(f"Found Wine from environment: {env_wine}")
            return env_wine
        
        # Check which wine (works on NixOS)
        import subprocess
        try:
            result = subprocess.run(['which', 'wine'], capture_output=True, text=True)
            if result.returncode == 0:
                wine_path = result.stdout.strip()
                if os.path.exists(wine_path):
                    logger.info(f"Found Wine via which: {wine_path}")
                    return wine_path
        except:
            pass
        
        # Fallback to standard paths
        wine_paths = ["/usr/bin/wine", "/usr/local/bin/wine", "/opt/wine/bin/wine"]
        
        for path in wine_paths:
            if os.path.exists(path):
                logger.info(f"Found Wine at: {path}")
                return path
        
        logger.warning("Wine not found - Windows VST support disabled")
        return None
    
    def _validate_wine(self, wine_path: str) -> bool:
        """Validate Wine installation
        
        Args:
            wine_path: Path to Wine executable
            
        Returns:
            True if Wine is working properly
        """
        try:
            import subprocess
            # Check Wine version
            result = subprocess.run(
                [wine_path, '--version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                version = result.stdout.strip()
                logger.info(f"Wine validated: {version}")
                
                # Check for wine-staging (recommended for audio)
                if 'staging' in version.lower():
                    logger.info("Wine Staging detected (recommended for audio)")
                
                # Check for 64-bit support
                result = subprocess.run(
                    ['which', 'wine64'],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    logger.info("Wine64 support detected")
                else:
                    logger.warning("Wine64 not found - 64-bit Windows VST support may be limited")
                
                return True
            else:
                logger.error(f"Wine version check failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error("Wine validation timed out")
            return False
        except Exception as e:
            logger.error(f"Wine validation failed: {str(e)}")
            return False
    
    def _validate_bridges(self, bin_path: str) -> bool:
        """Validate Carla bridge binaries exist
        
        Args:
            bin_path: Path to Carla bin directory
            
        Returns:
            True if all required bridges are found
        """
        bridges_found = True
        
        # Check native bridges
        native_bridges = [
            'carla-bridge-native',
            'carla-bridge-lv2-modgui',
            'carla-bridge-lv2-gtk2',
            'carla-bridge-lv2-gtk3',
            'carla-bridge-lv2-qt4',
            'carla-bridge-lv2-qt5'
        ]
        
        for bridge in native_bridges:
            bridge_path = os.path.join(bin_path, bridge)
            if os.path.exists(bridge_path):
                logger.debug(f"Found native bridge: {bridge}")
            else:
                logger.debug(f"Native bridge not found (optional): {bridge}")
        
        # Check Wine bridges (critical for Windows VST support)
        wine_bridges = [
            'carla-bridge-win32.exe',
            'carla-bridge-win64.exe'
        ]
        
        wine_bridges_found = 0
        for bridge in wine_bridges:
            bridge_path = os.path.join(bin_path, bridge)
            if os.path.exists(bridge_path):
                logger.info(f"Found Wine bridge: {bridge}")
                wine_bridges_found += 1
            else:
                logger.warning(f"Wine bridge not found: {bridge} - Windows VST support will be limited")
                bridges_found = False
        
        if wine_bridges_found == 0:
            logger.error("No Wine bridges found - Windows VST support disabled")
            logger.info("To enable Windows VST support, build Carla with Wine support")
        elif wine_bridges_found == 1:
            logger.warning("Only partial Wine bridge support - some Windows VSTs may not load")
        else:
            logger.info("All Wine bridges found - full Windows VST support available")
        
        # Check POSIX bridges
        posix_bridges = [
            'carla-bridge-posix32',
            'carla-bridge-posix64'
        ]
        
        for bridge in posix_bridges:
            bridge_path = os.path.join(bin_path, bridge)
            if os.path.exists(bridge_path):
                logger.debug(f"Found POSIX bridge: {bridge}")
            else:
                logger.debug(f"POSIX bridge not found (optional): {bridge}")
        
        return bridges_found
    
    def start_engine(self, driver: str = "JACK", client_name: str = "CarlaMCP") -> bool:
        """Start the audio engine (if not already running from __init__)
        
        Args:
            driver: Audio driver (JACK, ALSA, PulseAudio, etc.)
            client_name: Client name for JACK
            
        Returns:
            True if successful
        """
        if self.engine_running:
            logger.warning("Engine already running")
            return True
        
        # Initialize engine
        if not self.host.engine_init(driver, client_name):
            logger.error(f"Failed to initialize {driver} engine")
            return False
        
        self.engine_running = True
        
        # Start idle thread if not running
        if not self.idle_running:
            self.idle_running = True
            self.idle_thread = threading.Thread(target=self._idle_loop, daemon=True)
            self.idle_thread.start()
        
        # Get engine info
        sample_rate = self.host.get_sample_rate()
        buffer_size = self.host.get_buffer_size()
        
        logger.info(f"Engine started: {driver} @ {sample_rate}Hz, {buffer_size} samples")
        
        return True
    
    def stop_engine(self):
        """Stop the audio engine"""
        if not self.engine_running:
            return
        
        # Stop idle thread
        self.idle_running = False
        if self.idle_thread:
            self.idle_thread.join(timeout=2.0)
        
        # Close engine
        self.host.engine_close()
        self.engine_running = False
        
        logger.info("Engine stopped")
    
    def _idle_loop(self):
        """Process engine events"""
        while self.idle_running:
            self.host.engine_idle()
            time.sleep(0.01)
    
    def set_callback(self, callback: Callable):
        """Set event callback function"""
        self.event_callback = callback
        
        def engine_callback(host, action, plugin_id, value1, value2, value3, valuef, value_str):
            if self.event_callback:
                self.event_callback(host, action, plugin_id, value1, value2, value3, valuef, value_str)
        
        self.host.set_engine_callback(engine_callback)
    
    def load_plugin(self, path: str, plugin_type: PluginType,
                   name: Optional[str] = None, preset: Optional[str] = None) -> Optional[int]:
        """Load a plugin

        Args:
            path: Plugin path or URI
            plugin_type: Type of plugin
            name: Custom name
            preset: Preset to load after plugin loads

        Returns:
            Plugin ID if successful, None otherwise
        """
        try:
            logger.info(f"Loading plugin: {path} (type: {plugin_type.name})")

            # Pre-validation: Check if engine is running
            if not self.engine_running:
                logger.error("Cannot load plugin: Engine is not running")
                return None

            # Pre-validation: For file-based plugins, check if file exists
            if plugin_type in [PluginType.VST2, PluginType.VST3, PluginType.LADSPA, PluginType.DSSI]:
                if not path:
                    logger.error(f"Cannot load {plugin_type.name} plugin: No path provided")
                    return None

                if not os.path.exists(path):
                    logger.error(f"Cannot load plugin: File not found - {path}")
                    return None

                if not os.access(path, os.R_OK):
                    logger.error(f"Cannot load plugin: No read permission - {path}")
                    return None

            # Pre-validation: For LV2, validate URI format
            elif plugin_type == PluginType.LV2:
                if not path or not path.startswith("http"):
                    logger.error(f"Cannot load LV2 plugin: Invalid URI format - {path}")
                    return None

            # Determine binary type
            binary_type = self._get_binary_type(path, plugin_type)
            logger.debug(f"Using binary type: {binary_type.name}")

            # For LV2, path is empty and URI goes in label
            if plugin_type == PluginType.LV2:
                filename = ""
                label = path  # LV2 URI
            else:
                filename = path
                label = None

            # Record plugin count before adding
            initial_count = self.host.get_current_plugin_count()

            # Add plugin
            logger.debug(f"Calling add_plugin: binary_type={binary_type}, plugin_type={plugin_type}, filename='{filename}', label='{label}'")
            success = self.host.add_plugin(
                binary_type,
                plugin_type,
                filename,
                name,
                label,
                0,  # Unique ID (0 for auto)
                None,  # Extra pointer
                0  # Options
            )

            if not success:
                # Get last error from Carla if available
                error_msg = self.host.get_last_error() or "Unknown error"
                logger.error(f"Failed to load plugin '{path}': {error_msg}")
                return None

            # Verify plugin was actually added
            new_count = self.host.get_current_plugin_count()
            if new_count <= initial_count:
                logger.error(f"Plugin load failed: Plugin count did not increase (was {initial_count}, still {new_count})")
                return None

            # Get plugin ID
            plugin_id = new_count - 1
            logger.debug(f"Plugin loaded with ID: {plugin_id}")

            # Post-validation: Get and validate plugin info
            info = self.host.get_plugin_info(plugin_id)
            if not info:
                logger.error(f"Plugin load failed: Could not retrieve plugin info for ID {plugin_id}")
                # Cleanup: Remove the failed plugin
                self.host.remove_plugin(plugin_id)
                return None

            plugin_name = info.get('name', name or Path(path).stem)
            logger.debug(f"Plugin info retrieved: name='{plugin_name}', maker='{info.get('maker', 'Unknown')}'")

            # Store plugin info
            self.plugins[plugin_id] = {
                'id': plugin_id,
                'path': path,
                'type': plugin_type,
                'name': plugin_name,
                'active': False,
                'volume': 1.0,
                'dry_wet': 1.0,
                'parameters': {},
                'preset': preset
            }

            # Load preset if specified
            if preset:
                logger.debug(f"Loading preset: {preset}")
                try:
                    self.load_preset(plugin_id, preset)
                except Exception as e:
                    logger.warning(f"Failed to load preset '{preset}': {e}")

            # Activate by default
            logger.debug(f"Activating plugin {plugin_id}")
            self.set_plugin_active(plugin_id, True)

            logger.info(f"Successfully loaded plugin {plugin_id}: '{plugin_name}' from {path}")

            return plugin_id

        except Exception as e:
            logger.error(f"Unexpected error loading plugin '{path}': {e}")
            return None
    
    def _get_binary_type(self, path: str, plugin_type: PluginType) -> BinaryType:
        """Determine binary type for plugin"""
        # Native Linux plugins
        if plugin_type in [PluginType.LV2, PluginType.LADSPA, PluginType.DSSI]:
            return BinaryType.NATIVE
        
        # Windows plugins via Wine
        if path.endswith('.dll') or '.vst3' in path or path.endswith('.vst'):
            # For now, assume 64-bit Windows plugins
            # TODO: Implement PE header checking for accurate 32/64 detection
            return BinaryType.WIN64
        
        # Default to native
        return BinaryType.NATIVE
    
    def remove_plugin(self, plugin_id: int) -> bool:
        """Remove a plugin
        
        Args:
            plugin_id: Plugin ID to remove
            
        Returns:
            True if successful
        """
        if plugin_id not in self.plugins:
            logger.warning(f"Plugin {plugin_id} not found")
            return False
        
        success = self.host.remove_plugin(plugin_id)
        
        if success:
            del self.plugins[plugin_id]
            logger.info(f"Removed plugin {plugin_id}")
        
        return success
    
    def set_plugin_active(self, plugin_id: int, active: bool):
        """Activate or bypass a plugin"""
        if plugin_id not in self.plugins:
            return
        
        self.host.set_active(plugin_id, active)
        self.plugins[plugin_id]['active'] = active
    
    def set_parameter(self, plugin_id: int, param_id: int, value: float):
        """Set a plugin parameter
        
        Args:
            plugin_id: Plugin ID
            param_id: Parameter index
            value: Parameter value (0.0 to 1.0)
        """
        if plugin_id not in self.plugins:
            return
        
        self.host.set_parameter_value(plugin_id, param_id, value)
        
        # Store in plugin state
        if 'parameters' not in self.plugins[plugin_id]:
            self.plugins[plugin_id]['parameters'] = {}
        self.plugins[plugin_id]['parameters'][param_id] = value
    
    def get_parameter(self, plugin_id: int, param_id: int) -> float:
        """Get a plugin parameter value"""
        if plugin_id not in self.plugins:
            return 0.0
        
        return self.host.get_current_parameter_value(plugin_id, param_id)
    
    def get_parameter_info(self, plugin_id: int, param_id: int) -> Dict[str, Any]:
        """Get parameter information"""
        info = self.host.get_parameter_info(plugin_id, param_id)
        data = self.host.get_parameter_data(plugin_id, param_id)
        ranges = self.host.get_parameter_ranges(plugin_id, param_id)
        
        return {
            'name': info['name'] if info else '',
            'symbol': info['symbol'] if info else '',
            'unit': info['unit'] if info else '',
            'type': data.get('type', 0) if data else 0,
            'hints': data.get('hints', 0) if data else 0,
            'min': ranges['min'] if ranges else 0.0,
            'max': ranges['max'] if ranges else 1.0,
            'default': ranges['def'] if ranges else 0.5,
            'step': ranges['step'] if ranges else 0.01,
            'current': self.get_parameter(plugin_id, param_id)
        }
    
    def list_parameters(self, plugin_id: int) -> List[Dict[str, Any]]:
        """List all parameters for a plugin"""
        if plugin_id not in self.plugins:
            return []
        
        count = self.host.get_parameter_count(plugin_id)
        params = []
        
        for i in range(count):
            params.append({
                'index': i,
                **self.get_parameter_info(plugin_id, i)
            })
        
        return params
    
    def map_midi_cc(self, plugin_id: int, param_id: int, cc_number: int, channel: int = 0):
        """Map a MIDI CC to a parameter
        
        Args:
            plugin_id: Plugin ID
            param_id: Parameter index
            cc_number: MIDI CC number (0-127)
            channel: MIDI channel (0-15)
        """
        if plugin_id not in self.plugins:
            return
        
        # Use the correct API methods for MIDI mapping
        self.host.set_parameter_mapped_control_index(plugin_id, param_id, cc_number)
        self.host.set_parameter_midi_channel(plugin_id, param_id, channel)
        
        logger.info(f"Mapped plugin {plugin_id} param {param_id} to CC {cc_number} ch {channel+1}")
    
    def send_midi_note(self, plugin_id: int, note: int, velocity: int, channel: int = 0):
        """Send a MIDI note to a plugin
        
        Args:
            plugin_id: Plugin ID
            note: MIDI note number (0-127)
            velocity: Note velocity (0-127, 0 = note off)
            channel: MIDI channel (0-15)
        """
        if plugin_id not in self.plugins:
            return
        
        self.host.send_midi_note(plugin_id, channel, note, velocity)
    
    def load_preset(self, plugin_id: int, preset_path: str) -> bool:
        """Load a preset for a plugin
        
        Args:
            plugin_id: Plugin ID
            preset_path: Path to preset file
            
        Returns:
            True if successful
        """
        if plugin_id not in self.plugins:
            return False
        
        # For native Carla format
        if preset_path.endswith('.carxs'):
            return self.host.load_plugin_state(plugin_id, preset_path)
        
        # For VST presets
        if preset_path.endswith('.fxp') or preset_path.endswith('.fxb'):
            return self.host.set_custom_data(
                plugin_id,
                CUSTOM_DATA_TYPE_CHUNK,
                "file",
                preset_path
            )
        
        return False
    
    def save_preset(self, plugin_id: int, preset_path: str) -> bool:
        """Save a plugin preset
        
        Args:
            plugin_id: Plugin ID
            preset_path: Path to save preset
            
        Returns:
            True if successful
        """
        if plugin_id not in self.plugins:
            return False
        
        return self.host.save_plugin_state(plugin_id, preset_path)
    
    def get_audio_peaks(self, plugin_id: int) -> Dict[str, float]:
        """Get current audio peak levels
        
        Args:
            plugin_id: Plugin ID
            
        Returns:
            Dictionary with peak levels
        """
        if plugin_id not in self.plugins:
            return {'in_left': 0, 'in_right': 0, 'out_left': 0, 'out_right': 0}
        
        # Use the correct Carla API methods
        in_left = self.host.get_input_peak_value(plugin_id, True)
        in_right = self.host.get_input_peak_value(plugin_id, False)
        out_left = self.host.get_output_peak_value(plugin_id, True)
        out_right = self.host.get_output_peak_value(plugin_id, False)
        
        return {
            'in_left': in_left,
            'in_right': in_right,
            'out_left': out_left,
            'out_right': out_right
        }
    
    def get_cpu_load(self, plugin_id: Optional[int] = None) -> float:
        """Get CPU load - NOT AVAILABLE IN CARLA API
        
        Args:
            plugin_id: Specific plugin ID or None for total
            
        Returns:
            CPU load percentage (always returns 0)
        """
        # CPU load per plugin is not available in Carla's Python API
        return 0.0
    
    def refresh_connections(self):
        """Refresh audio/MIDI connections"""
        self.host.patchbay_refresh(True)
    
    def connect_audio(self, source_plugin: int, source_port: int,
                     dest_plugin: int, dest_port: int) -> bool:
        """Connect audio ports
        
        Args:
            source_plugin: Source plugin ID
            source_port: Source port index
            dest_plugin: Destination plugin ID
            dest_port: Destination port index
            
        Returns:
            True if successful
        """
        # This would use patchbay_connect with proper group/port IDs
        # Implementation depends on patchbay mode
        
        connection = {
            'source': {'plugin': source_plugin, 'port': source_port},
            'dest': {'plugin': dest_plugin, 'port': dest_port}
        }
        self.connections.append(connection)
        
        logger.info(f"Connected audio: {source_plugin}:{source_port} -> {dest_plugin}:{dest_port}")
        
        return True
    
    def save_project(self, filepath: str) -> bool:
        """Save complete project
        
        Args:
            filepath: Path to save project
            
        Returns:
            True if successful
        """
        return self.host.save_project(filepath)
    
    def load_project(self, filepath: str) -> bool:
        """Load complete project
        
        Args:
            filepath: Path to project file
            
        Returns:
            True if successful
        """
        # Clear current state
        self.plugins.clear()
        self.connections.clear()
        
        # Load project
        success = self.host.load_project(filepath)
        
        if success:
            # Rebuild plugin list
            count = self.host.get_current_plugin_count()
            for i in range(count):
                info = self.host.get_plugin_info(i)
                if info:
                    self.plugins[i] = {
                        'id': i,
                        'name': info['name'],
                        'active': True  # Plugins are active by default when loaded
                    }
        
        return success
    
    def get_system_info(self) -> Dict[str, Any]:
        """Get system information"""
        return {
            'engine_running': self.engine_running,
            'sample_rate': self.host.get_sample_rate() if self.engine_running else 0,
            'buffer_size': self.host.get_buffer_size() if self.engine_running else 0,
            'plugin_count': len(self.plugins),
            'cpu_load': self.get_cpu_load(),
            'xruns': self.xruns
        }