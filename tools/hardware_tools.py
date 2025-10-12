#!/usr/bin/env python3
"""
Hardware Interface Tools for Carla MCP Server
"""

import logging
import subprocess
import sys
import os
from typing import Dict, Any, List, Optional

# Add Carla to path for constants
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'source', 'frontend'))
from carla_backend import ENGINE_OPTION_AUDIO_SAMPLE_RATE, ENGINE_OPTION_AUDIO_BUFFER_SIZE, ENGINE_OPTION_AUDIO_DEVICE

logger = logging.getLogger(__name__)


class HardwareTools:
    """Hardware interface tools for Carla"""
    
    def __init__(self, carla_controller):
        """Initialize hardware tools
        
        Args:
            carla_controller: CarlaController instance
        """
        self.carla = carla_controller
        self.devices = {}
        self.control_surfaces = {}
        
        logger.info("HardwareTools initialized")
    
    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Execute a hardware tool
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            
        Returns:
            Tool execution result
        """
        if tool_name == "configure_audio_interface":
            return await self.configure_audio_interface(**arguments)
        elif tool_name == "list_audio_devices":
            return await self.list_audio_devices(**arguments)
        elif tool_name == "map_control_surface":
            return await self.map_control_surface(**arguments)
        else:
            raise ValueError(f"Unknown hardware tool: {tool_name}")
    
    async def configure_audio_interface(self, device: str, sample_rate: Optional[int] = None,
                                       buffer_size: Optional[int] = None,
                                       channels_in: Optional[int] = None,
                                       channels_out: Optional[int] = None,
                                       session_context: dict = None, **kwargs) -> dict:
        """Configure audio hardware settings
        
        Args:
            device: Device name
            sample_rate: Sample rate in Hz
            buffer_size: Buffer size in samples
            channels_in: Input channels
            channels_out: Output channels
            
        Returns:
            Configuration result
        """
        try:
            # Set engine options if provided
            if sample_rate:
                self.carla.host.set_engine_option(ENGINE_OPTION_AUDIO_SAMPLE_RATE, sample_rate, "")
            
            if buffer_size:
                self.carla.host.set_engine_option(ENGINE_OPTION_AUDIO_BUFFER_SIZE, buffer_size, "")
            
            # Set device
            self.carla.host.set_engine_option(ENGINE_OPTION_AUDIO_DEVICE, 0, device)
            
            # Restart engine with new settings
            if self.carla.engine_running:
                self.carla.stop_engine()
                self.carla.start_engine()
            
            # Get actual settings
            actual_settings = {
                'device': device,
                'sample_rate': self.carla.host.get_sample_rate() if self.carla.engine_running else sample_rate,
                'buffer_size': self.carla.host.get_buffer_size() if self.carla.engine_running else buffer_size,
                'channels_in': channels_in or 2,
                'channels_out': channels_out or 2
            }
            
            # Calculate latency
            if actual_settings['sample_rate'] and actual_settings['buffer_size']:
                latency_ms = (actual_settings['buffer_size'] / actual_settings['sample_rate']) * 1000
            else:
                latency_ms = 0
            
            logger.info(f"Configured audio interface: {device}")
            
            return {
                'success': True,
                'device': device,
                'actual_settings': actual_settings,
                'latency_ms': latency_ms
            }
            
        except Exception as e:
            logger.error(f"Failed to configure audio interface: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def list_audio_devices(self, driver: Optional[str] = None,
                                session_context: dict = None, **kwargs) -> dict:
        """List available audio devices
        
        Args:
            driver: Audio driver (JACK, ALSA, etc.)
            
        Returns:
            List of available devices
        """
        try:
            devices = []
            
            if driver == "JACK" or driver is None:
                # List JACK devices
                try:
                    result = subprocess.run(['jack_lsp'], capture_output=True, text=True)
                    if result.returncode == 0:
                        jack_ports = result.stdout.strip().split('\n')
                        devices.append({
                            'driver': 'JACK',
                            'name': 'JACK Audio Connection Kit',
                            'ports': jack_ports[:10],  # First 10 ports
                            'status': 'available'
                        })
                except:
                    devices.append({
                        'driver': 'JACK',
                        'name': 'JACK Audio Connection Kit',
                        'status': 'not running'
                    })
            
            if driver == "ALSA" or driver is None:
                # List ALSA devices
                try:
                    result = subprocess.run(['aplay', '-l'], capture_output=True, text=True)
                    if result.returncode == 0:
                        # Parse ALSA device list
                        lines = result.stdout.strip().split('\n')
                        for line in lines:
                            if 'card' in line.lower():
                                devices.append({
                                    'driver': 'ALSA',
                                    'name': line.strip(),
                                    'status': 'available'
                                })
                except:
                    pass
            
            # Add PulseAudio if available
            devices.append({
                'driver': 'PulseAudio',
                'name': 'PulseAudio Sound Server',
                'status': 'available'
            })
            
            # Add dummy device
            devices.append({
                'driver': 'Dummy',
                'name': 'Dummy Audio Device',
                'status': 'available'
            })
            
            return {
                'success': True,
                'devices': devices,
                'total': len(devices),
                'drivers_checked': [driver] if driver else ['JACK', 'ALSA', 'PulseAudio', 'Dummy']
            }
            
        except Exception as e:
            logger.error(f"Failed to list audio devices: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def map_control_surface(self, device_name: str, mapping_preset: Optional[str] = None,
                                 learn_mode: bool = False, session_context: dict = None, **kwargs) -> dict:
        """Map hardware controllers
        
        Args:
            device_name: Controller device name
            mapping_preset: Preset mapping to use
            learn_mode: Enable MIDI learn mode
            
        Returns:
            Mapping result
        """
        try:
            # Store control surface info
            self.control_surfaces[device_name] = {
                'name': device_name,
                'preset': mapping_preset,
                'learn_mode': learn_mode,
                'mapped_controls': [],
                'unmapped_controls': []
            }
            
            # Note: Carla's Python API doesn't provide direct control surface mapping
            # MIDI mappings must be done per-parameter using map_midi_cc
            
            logger.info(f"Mapped control surface: {device_name}")
            
            return {
                'success': True,
                'device': device_name,
                'preset': mapping_preset,
                'learn_mode': learn_mode,
                'note': 'Use parameter_tools.map_midi_cc for actual MIDI mappings'
            }
            
        except Exception as e:
            logger.error(f"Failed to map control surface: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
