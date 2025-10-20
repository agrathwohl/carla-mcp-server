#!/usr/bin/env python3
"""
Plugin Control Tools for Carla MCP Server
"""

import os
import time
import logging
import subprocess
from typing import Dict, Any, List, Optional
from pathlib import Path
import threading
import wave
import numpy as np
from carla_controller import PluginType
from base_tools import validate_plugin_id

logger = logging.getLogger(__name__)

# Plugin type string to enum mapping
PLUGIN_TYPE_MAP = {
    'VST2': PluginType.VST2,
    'VST3': PluginType.VST3,
    'LV2': PluginType.LV2,
    'LADSPA': PluginType.LADSPA,
    'DSSI': PluginType.DSSI,
    'AU': PluginType.AU,
    'SF2': PluginType.SF2,
    'SFZ': PluginType.SFZ,
    'JACK': PluginType.JACK
}


def parse_plugin_type(type_str: str) -> PluginType:
    """Convert plugin type string to PluginType enum.

    Args:
        type_str: Plugin type as string (case-insensitive)

    Returns:
        PluginType enum value

    Raises:
        ValueError: If plugin type is unknown
    """
    plugin_type = PLUGIN_TYPE_MAP.get(type_str.upper())
    if plugin_type is None:
        valid_types = ', '.join(PLUGIN_TYPE_MAP.keys())
        raise ValueError(f"Unknown plugin type '{type_str}'. Valid types: {valid_types}")
    return plugin_type


class PluginTools:
    """Plugin management and control tools for Carla"""
    
    def __init__(self, carla_controller):
        """Initialize plugin tools
        
        Args:
            carla_controller: CarlaController instance
        """
        self.carla = carla_controller
        self.plugin_cache = {}
        self.processing_queue = []
        
        logger.info("PluginTools initialized")
    
    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Execute a plugin tool
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            
        Returns:
            Tool execution result
        """
        if tool_name == "load_plugin":
            return await self.load_plugin(**arguments)
        elif tool_name == "scan_plugins":
            return await self.scan_plugins(**arguments)
        elif tool_name == "control_plugin":
            return await self.control_plugin(**arguments)
        elif tool_name == "batch_process":
            return await self.batch_process(**arguments)
        elif tool_name == "list_plugins":
            return await self.list_plugins(**arguments)
        elif tool_name == "get_plugin_info":
            return await self.get_plugin_info(**arguments)
        elif tool_name == "clone_plugin":
            return await self.clone_plugin(**arguments)
        elif tool_name == "replace_plugin":
            return await self.replace_plugin(**arguments)
        else:
            raise ValueError(f"Unknown plugin tool: {tool_name}")
    
    async def load_plugin(self, path: str, type: str, position: int = -1,
                         preset: Optional[str] = None, session_context: dict = None, **kwargs) -> dict:
        """Load a plugin
        
        Args:
            path: Plugin path or URI
            type: Plugin type (VST2, VST3, LV2, etc.)
            position: Rack position (-1 for end)
            preset: Optional preset to load
            
        Returns:
            Plugin information
        """
        try:
            # Ensure engine is running
            if not self.carla.engine_running:
                if not self.carla.start_engine():
                    raise Exception("Failed to start Carla engine - cannot load plugins")

            # Convert type string to enum
            plugin_type = parse_plugin_type(type)

            # Load the plugin
            plugin_id = self.carla.load_plugin(path, plugin_type, preset=preset)
            
            if plugin_id is None:
                raise Exception(f"Failed to load plugin: {path}")
            
            # Get plugin information
            info = self.carla.host.get_plugin_info(plugin_id)
            
            # Get parameter list
            parameters = self.carla.list_parameters(plugin_id)
            
            # Get I/O configuration using the correct API methods
            audio_info = self.carla.host.get_audio_port_count_info(plugin_id)
            midi_info = self.carla.host.get_midi_port_count_info(plugin_id)
            
            io_config = {
                'audio_ins': audio_info.get('ins', 0) if audio_info else 0,
                'audio_outs': audio_info.get('outs', 0) if audio_info else 0,
                'midi_ins': midi_info.get('ins', 0) if midi_info else 0,
                'midi_outs': midi_info.get('outs', 0) if midi_info else 0,
                'cv_ins': 0,  # CV ports not directly accessible via API
                'cv_outs': 0  # CV ports not directly accessible via API
            }
            
            # Store in cache
            self.plugin_cache[plugin_id] = {
                'path': path,
                'type': type,
                'info': info,
                'parameters': parameters,
                'io_config': io_config
            }
            
            logger.info(f"Loaded plugin {plugin_id}: {info.get('name', path) if info else path}")
            
            return {
                'success': True,
                'plugin_id': plugin_id,
                'name': info.get('name', Path(path).stem) if info else Path(path).stem,
                'maker': info.get('maker', 'Unknown') if info else 'Unknown',
                'category': info.get('category', 'Unknown') if info else 'Unknown',
                'parameters': len(parameters),
                'io_config': io_config,
                'position': position
            }
            
        except Exception as e:
            logger.error(f"Failed to load plugin: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def scan_plugins(self, directory: str, formats: Optional[List[str]] = None,
                          recursive: bool = True, session_context: dict = None, **kwargs) -> dict:
        """Scan directory for plugins
        
        Args:
            directory: Directory to scan
            formats: Plugin formats to scan (None for all)
            recursive: Scan recursively
            
        Returns:
            List of found plugins
        """
        try:
            if formats is None:
                formats = ['VST2', 'VST3', 'LV2']
            
            found_plugins = []
            errors = []
            
            # Define file extensions for each format
            format_extensions = {
                'VST2': ['.dll', '.so', '.dylib'],
                'VST3': ['.vst3'],
                'LV2': ['.lv2'],
                'LADSPA': ['.so'],
                'DSSI': ['.so']
            }
            
            # Scan directory
            path = Path(directory)
            
            if not path.exists():
                raise Exception(f"Directory not found: {directory}")
            
            # Get all files
            if recursive:
                files = list(path.rglob('*'))
            else:
                files = list(path.glob('*'))
            
            # Filter by format
            for file_path in files:
                # Check files for VST2, LADSPA, DSSI plugins
                if file_path.is_file():
                    for format_type in formats:
                        extensions = format_extensions.get(format_type, [])

                        if any(str(file_path).endswith(ext) for ext in extensions):
                            # Try to get plugin info (quick scan)
                            plugin_info = {
                                'path': str(file_path),
                                'name': file_path.stem,
                                'format': format_type,
                                'size': file_path.stat().st_size
                            }

                            found_plugins.append(plugin_info)
                            logger.debug(f"Found {format_type} plugin: {file_path}")

                # Check directories for LV2 and VST3 bundles
                elif file_path.is_dir():
                    for format_type in formats:
                        extensions = format_extensions.get(format_type, [])

                        if any(str(file_path).endswith(ext) for ext in extensions):
                            # Calculate directory size
                            try:
                                size = sum(f.stat().st_size for f in file_path.rglob('*') if f.is_file())
                            except:
                                size = 0

                            plugin_info = {
                                'path': str(file_path),
                                'name': file_path.stem,
                                'format': format_type,
                                'size': size
                            }

                            found_plugins.append(plugin_info)
                            logger.debug(f"Found {format_type} bundle: {file_path}")

                # Legacy VST3 handling (kept for compatibility)
                elif file_path.is_dir() and file_path.suffix == '.vst3' and 'VST3' in formats:
                    plugin_info = {
                        'path': str(file_path),
                        'name': file_path.stem,
                        'format': 'VST3',
                        'size': sum(f.stat().st_size for f in file_path.rglob('*') if f.is_file())
                    }
                    
                    found_plugins.append(plugin_info)
                    logger.debug(f"Found VST3 bundle: {file_path}")
            
            # Sort by name
            found_plugins.sort(key=lambda x: x['name'].lower())
            
            logger.info(f"Scanned {directory}: found {len(found_plugins)} plugins")
            
            return {
                'success': True,
                'plugins': found_plugins,
                'total': len(found_plugins),
                'formats_scanned': formats,
                'errors': errors,
                'directory': directory
            }
            
        except Exception as e:
            logger.error(f"Failed to scan plugins: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def control_plugin(self, plugin_id: str, action: str, fade_ms: int = 0,
                           session_context: dict = None, **kwargs) -> dict:
        """Control plugin state

        Args:
            plugin_id: Plugin ID
            action: Action to perform (activate, bypass, solo, remove)
            fade_ms: Fade time in milliseconds

        Returns:
            New plugin state
        """
        try:
            plugin_id = validate_plugin_id(plugin_id, self.carla)

            if action == "activate":
                if fade_ms > 0:
                    # Fade in
                    await self._fade_plugin(plugin_id, 0.0, 1.0, fade_ms)
                self.carla.set_plugin_active(plugin_id, True)
                
            elif action == "bypass":
                if fade_ms > 0:
                    # Fade out
                    await self._fade_plugin(plugin_id, 1.0, 0.0, fade_ms)
                self.carla.set_plugin_active(plugin_id, False)
                
            elif action == "solo":
                # Bypass all other plugins
                for pid in self.carla.plugins:
                    if pid != plugin_id:
                        self.carla.set_plugin_active(pid, False)
                self.carla.set_plugin_active(plugin_id, True)
                
            elif action == "remove":
                if fade_ms > 0:
                    # Fade out before removing
                    await self._fade_plugin(plugin_id, 1.0, 0.0, fade_ms)
                success = self.carla.remove_plugin(plugin_id)
                
                if not success:
                    raise Exception(f"Failed to remove plugin {plugin_id}")
                
                # Remove from cache
                if plugin_id in self.plugin_cache:
                    del self.plugin_cache[plugin_id]
                
                return {
                    'success': True,
                    'plugin_id': plugin_id,
                    'action': action,
                    'removed': True
                }
            else:
                raise ValueError(f"Unknown action: {action}")
            
            # Get new state
            new_state = {
                'active': self.carla.plugins[plugin_id]['active'] if plugin_id in self.carla.plugins else False,
                'volume': self.carla.plugins.get(plugin_id, {}).get('volume', 1.0),
                'cpu_usage': self.carla.get_cpu_load(plugin_id)
            }
            
            logger.info(f"Plugin {plugin_id} action: {action}")
            
            return {
                'success': True,
                'plugin_id': plugin_id,
                'action': action,
                'new_state': new_state,
                'fade_applied': fade_ms > 0
            }
            
        except Exception as e:
            logger.error(f"Failed to control plugin: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def _fade_plugin(self, plugin_id: int, start_vol: float, end_vol: float, duration_ms: int):
        """Fade plugin volume
        
        Args:
            plugin_id: Plugin ID
            start_vol: Starting volume (0.0 to 1.0)
            end_vol: Ending volume (0.0 to 1.0)
            duration_ms: Fade duration in milliseconds
        """
        steps = int(duration_ms / 10)  # 10ms steps
        
        for i in range(steps):
            progress = i / steps
            volume = start_vol + (end_vol - start_vol) * progress
            # Note: Carla doesn't have set_volume method, use internal state
            if plugin_id in self.carla.plugins:
                self.carla.plugins[plugin_id]['volume'] = volume
            time.sleep(0.01)
        
        # Set final volume
        if plugin_id in self.carla.plugins:
            self.carla.plugins[plugin_id]['volume'] = end_vol
            # Use Carla's set_volume method
            self.carla.host.set_volume(plugin_id, end_vol)
    
    async def batch_process(self, input_file: str, plugin_chain: List[str],
                          output_format: Optional[dict] = None, normalize: bool = True,
                          session_context: dict = None, **kwargs) -> dict:
        """Apply plugin chain to audio file
        
        NOTE: Carla's Python API does not provide offline/batch rendering.
        This sets up the plugin chain which can be used for real-time processing.
        
        Args:
            input_file: Input audio file path
            plugin_chain: List of plugin IDs to apply
            output_format: Output format settings
            normalize: Normalize output
            
        Returns:
            Processing setup result
        """
        try:
            if output_format is None:
                output_format = {
                    'sample_rate': 48000,
                    'bit_depth': 24,
                    'format': 'wav'
                }
            
            # Verify all plugins in chain exist
            for plugin_id_str in plugin_chain:
                plugin_id = int(plugin_id_str)
                if plugin_id not in self.carla.plugins:
                    raise ValueError(f"Plugin {plugin_id} not found")
            
            # Set up the plugin chain connections
            connections_made = []
            if len(plugin_chain) > 1:
                for i in range(len(plugin_chain) - 1):
                    src_id = int(plugin_chain[i])
                    dst_id = int(plugin_chain[i + 1])
                    
                    # Connect plugins in series
                    success = self.carla.connect_audio(src_id, 0, dst_id, 0)
                    if success:
                        connections_made.append(f"{src_id} -> {dst_id}")
            
            # Activate all plugins in chain
            for plugin_id_str in plugin_chain:
                plugin_id = int(plugin_id_str)
                self.carla.set_plugin_active(plugin_id, True)
            
            # Get real peak levels from the chain
            peak_data = {}
            for plugin_id_str in plugin_chain:
                plugin_id = int(plugin_id_str)
                peaks = self.carla.get_audio_peaks(plugin_id)
                peak_data[plugin_id] = peaks
            
            logger.info(f"Set up plugin chain with {len(plugin_chain)} plugins")
            
            return {
                'success': True,
                'note': 'Carla Python API does not support offline rendering. Plugin chain configured for real-time processing.',
                'input_file': input_file,
                'plugin_chain': plugin_chain,
                'connections_made': connections_made,
                'plugins_activated': len(plugin_chain),
                'peak_data': peak_data,
                'output_format': output_format,
                'real_time_processing': True
            }
            
        except Exception as e:
            logger.error(f"Failed to set up plugin chain: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def list_plugins(self, session_context: dict = None, **kwargs) -> dict:
        """List all loaded plugins
        
        Returns:
            List of loaded plugins
        """
        try:
            plugins = []
            
            for plugin_id, plugin_data in self.carla.plugins.items():
                info = self.carla.host.get_plugin_info(plugin_id)
                audio_info = self.carla.host.get_audio_port_count_info(plugin_id)
                
                plugins.append({
                    'id': plugin_id,
                    'name': plugin_data['name'],
                    'type': plugin_data.get('type', 'Unknown'),
                    'active': plugin_data['active'],
                    'cpu_usage': self.carla.get_cpu_load(plugin_id),
                    'latency': 0,  # Latency not available in Carla API
                    'parameters': self.carla.host.get_parameter_count(plugin_id),
                    'audio_ins': audio_info.get('ins', 0) if audio_info else 0,
                    'audio_outs': audio_info.get('outs', 0) if audio_info else 0
                })
            
            return {
                'success': True,
                'plugins': plugins,
                'total': len(plugins),
                'total_cpu': sum(p['cpu_usage'] for p in plugins)
            }
            
        except Exception as e:
            logger.error(f"Failed to list plugins: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def get_plugin_info(self, plugin_id: str, session_context: dict = None, **kwargs) -> dict:
        """Get detailed plugin information

        Args:
            plugin_id: Plugin ID

        Returns:
            Detailed plugin information
        """
        try:
            plugin_id = validate_plugin_id(plugin_id, self.carla)
            
            # Get basic info
            info = self.carla.host.get_plugin_info(plugin_id)
            plugin_data = self.carla.plugins[plugin_id]
            
            # Get parameters
            parameters = self.carla.list_parameters(plugin_id)
            
            # Get current state using internal parameters (there are no get_ methods for these)
            # Internal parameters: 0=active, 1=drywet, 2=volume, 3=balance-left, 4=balance-right, 5=panning
            state = {
                'active': plugin_data['active'],
                'volume': self.carla.plugins.get(plugin_id, {}).get('volume', 1.0),
                'drywet': self.carla.host.get_internal_parameter_value(plugin_id, 1) if plugin_id >= 0 else 1.0,
                'balance_left': self.carla.host.get_internal_parameter_value(plugin_id, 3) if plugin_id >= 0 else 0.0,
                'balance_right': self.carla.host.get_internal_parameter_value(plugin_id, 4) if plugin_id >= 0 else 0.0,
                'panning': self.carla.host.get_internal_parameter_value(plugin_id, 5) if plugin_id >= 0 else 0.0
            }
            
            # Get programs/presets
            program_count = self.carla.host.get_program_count(plugin_id)
            current_program = self.carla.host.get_current_program_index(plugin_id)
            
            programs = []
            for i in range(program_count):
                programs.append({
                    'index': i,
                    'name': self.carla.host.get_program_name(plugin_id, i),
                    'is_current': i == current_program
                })
            
            # Get audio peaks
            peaks = self.carla.get_audio_peaks(plugin_id)
            
            return {
                'success': True,
                'plugin_id': plugin_id,
                'name': info['name'] if info else plugin_data['name'],
                'label': info['label'] if info else '',
                'maker': info['maker'] if info else 'Unknown',
                'copyright': info['copyright'] if info else '',
                'unique_id': info['uniqueId'] if info else 0,
                'category': info['category'] if info else 'Unknown',
                'state': state,
                'parameters': parameters,
                'programs': programs,
                'current_program': current_program,
                'peaks': peaks,
                'cpu_usage': self.carla.get_cpu_load(plugin_id),
                'latency': 0  # Latency not available in Carla API
            }
            
        except Exception as e:
            logger.error(f"Failed to get plugin info: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def clone_plugin(self, plugin_id: str, session_context: dict = None, **kwargs) -> dict:
        """Clone a plugin with its current settings

        Args:
            plugin_id: Plugin ID to clone

        Returns:
            New plugin information
        """
        try:
            plugin_id = validate_plugin_id(plugin_id, self.carla)
            
            # Clone the plugin
            success = self.carla.host.clone_plugin(plugin_id)
            
            if not success:
                raise Exception(f"Failed to clone plugin {plugin_id}")
            
            # Get new plugin ID
            new_plugin_id = self.carla.host.get_current_plugin_count() - 1
            
            # Copy plugin data
            original_data = self.carla.plugins[plugin_id]
            self.carla.plugins[new_plugin_id] = original_data.copy()
            self.carla.plugins[new_plugin_id]['id'] = new_plugin_id
            
            logger.info(f"Cloned plugin {plugin_id} to {new_plugin_id}")
            
            return {
                'success': True,
                'original_id': plugin_id,
                'new_id': new_plugin_id,
                'name': original_data['name']
            }
            
        except Exception as e:
            logger.error(f"Failed to clone plugin: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def replace_plugin(self, plugin_id: str, new_path: str, new_type: str,
                           preserve_connections: bool = True, session_context: dict = None, **kwargs) -> dict:
        """Replace a plugin with another while preserving connections

        Args:
            plugin_id: Plugin ID to replace
            new_path: Path to new plugin
            new_type: New plugin type
            preserve_connections: Preserve audio connections

        Returns:
            Replacement result
        """
        try:
            plugin_id = validate_plugin_id(plugin_id, self.carla)
            
            # Store current connections if preserving
            connections = []
            if preserve_connections:
                # Store connections involving this plugin
                for conn in self.carla.connections:
                    if conn['source']['plugin'] == plugin_id or conn['dest']['plugin'] == plugin_id:
                        connections.append(conn.copy())
            
            # Remove old plugin
            success = self.carla.remove_plugin(plugin_id)

            if not success:
                raise Exception(f"Failed to remove plugin {plugin_id}")

            # Convert type string to enum and load new plugin
            plugin_type = parse_plugin_type(new_type)
            new_plugin_id = self.carla.load_plugin(new_path, plugin_type)
            
            if new_plugin_id is None:
                raise Exception(f"Failed to load replacement plugin: {new_path}")
            
            # Restore connections if requested
            if preserve_connections and connections:
                # Restore connections with the new plugin ID
                for conn in connections:
                    if conn['source']['plugin'] == plugin_id:
                        # This plugin was the source
                        self.carla.connect_audio(
                            new_plugin_id, conn['source']['port'],
                            conn['dest']['plugin'], conn['dest']['port']
                        )
                    elif conn['dest']['plugin'] == plugin_id:
                        # This plugin was the destination
                        self.carla.connect_audio(
                            conn['source']['plugin'], conn['source']['port'],
                            new_plugin_id, conn['dest']['port']
                        )
            
            logger.info(f"Replaced plugin {plugin_id} with {new_plugin_id}")
            
            return {
                'success': True,
                'old_id': plugin_id,
                'new_id': new_plugin_id,
                'new_path': new_path,
                'connections_preserved': preserve_connections
            }
            
        except Exception as e:
            logger.error(f"Failed to replace plugin: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }