#!/usr/bin/env python3
"""
JACK Port Management Tools for Carla MCP Server
Handles external JACK routing that Carla's API doesn't expose
"""

import logging
import subprocess
import re
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class JackTools:
    """JACK port management tools for external routing"""
    
    def __init__(self, carla_controller):
        """Initialize JACK tools
        
        Args:
            carla_controller: CarlaController instance
        """
        self.carla = carla_controller
        logger.info("JackTools initialized")
    
    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Execute a JACK tool
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            
        Returns:
            Tool execution result
        """
        if tool_name == "list_jack_ports":
            return await self.list_jack_ports(**arguments)
        elif tool_name == "connect_jack_ports":
            return await self.connect_jack_ports(**arguments)
        elif tool_name == "disconnect_jack_ports":
            return await self.disconnect_jack_ports(**arguments)
        elif tool_name == "get_jack_connections":
            return await self.get_jack_connections(**arguments)
        elif tool_name == "connect_system_to_plugin":
            return await self.connect_system_to_plugin(**arguments)
        elif tool_name == "connect_plugin_to_system":
            return await self.connect_plugin_to_system(**arguments)
        else:
            raise ValueError(f"Unknown JACK tool: {tool_name}")
    
    async def list_jack_ports(self, port_type: Optional[str] = None,
                             flags: Optional[str] = None,
                             name_pattern: Optional[str] = None,
                             session_context: dict = None, **kwargs) -> dict:
        """List available JACK ports
        
        Args:
            port_type: Filter by type (audio, midi)
            flags: Filter by flags (input, output, physical)
            name_pattern: Filter by name pattern
            
        Returns:
            List of JACK ports
        """
        try:
            cmd = ["jack_lsp"]
            
            # Add type filter
            if port_type == "audio":
                cmd.extend(["-t", "32 bit float mono audio"])
            elif port_type == "midi":
                cmd.extend(["-t", "8 bit raw midi"])
            
            # Add flag filters
            if flags:
                if "input" in flags:
                    cmd.append("-i")
                if "output" in flags:
                    cmd.append("-o")
                if "physical" in flags:
                    cmd.append("-p")
            
            # Execute command
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                raise Exception(f"jack_lsp failed: {result.stderr}")
            
            # Parse ports
            all_ports = result.stdout.strip().split('\n') if result.stdout.strip() else []
            
            # Filter by pattern if provided
            if name_pattern:
                import fnmatch
                all_ports = [p for p in all_ports if fnmatch.fnmatch(p.lower(), name_pattern.lower())]
            
            # Categorize ports
            system_ports = []
            carla_ports = []
            plugin_ports = []
            pulse_ports = []
            other_ports = []
            
            for port in all_ports:
                if port.startswith("system:"):
                    system_ports.append(port)
                elif "PulseAudio" in port:
                    pulse_ports.append(port)
                elif any(plugin in port for plugin in ["VU Meter", "Helm", "Reverb", "EQ"]):
                    plugin_ports.append(port)
                elif "Carla" in port or "carla" in port:
                    carla_ports.append(port)
                else:
                    other_ports.append(port)
            
            return {
                'success': True,
                'total': len(all_ports),
                'system_ports': system_ports,
                'carla_ports': carla_ports,
                'plugin_ports': plugin_ports,
                'pulse_ports': pulse_ports,
                'other_ports': other_ports,
                'filters_applied': {
                    'type': port_type,
                    'flags': flags,
                    'pattern': name_pattern
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to list JACK ports: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def connect_jack_ports(self, source: str, destination: str,
                                session_context: dict = None, **kwargs) -> dict:
        """Connect two JACK ports
        
        Args:
            source: Source port name
            destination: Destination port name
            
        Returns:
            Connection result
        """
        try:
            # Execute connection
            result = subprocess.run(
                ["jack_connect", source, destination],
                capture_output=True,
                text=True
            )
            
            # Check if already connected (exit code 0 or specific error)
            if result.returncode == 0:
                message = f"Connected: {source} -> {destination}"
            elif "already connected" in result.stderr.lower():
                message = f"Already connected: {source} -> {destination}"
            else:
                raise Exception(f"Connection failed: {result.stderr}")
            
            logger.info(message)
            
            return {
                'success': True,
                'source': source,
                'destination': destination,
                'message': message
            }
            
        except Exception as e:
            logger.error(f"Failed to connect JACK ports: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def disconnect_jack_ports(self, source: str, destination: str,
                                   session_context: dict = None, **kwargs) -> dict:
        """Disconnect two JACK ports
        
        Args:
            source: Source port name
            destination: Destination port name
            
        Returns:
            Disconnection result
        """
        try:
            result = subprocess.run(
                ["jack_disconnect", source, destination],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0 and "not connected" not in result.stderr.lower():
                raise Exception(f"Disconnection failed: {result.stderr}")
            
            message = f"Disconnected: {source} -X-> {destination}"
            logger.info(message)
            
            return {
                'success': True,
                'source': source,
                'destination': destination,
                'message': message
            }
            
        except Exception as e:
            logger.error(f"Failed to disconnect JACK ports: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def get_jack_connections(self, port: Optional[str] = None,
                                  session_context: dict = None, **kwargs) -> dict:
        """Get connections for a JACK port or all connections
        
        Args:
            port: Specific port to check (or None for all)
            
        Returns:
            Connection information
        """
        try:
            if port:
                # Get connections for specific port
                result = subprocess.run(
                    ["jack_lsp", "-c", port],
                    capture_output=True,
                    text=True
                )
                
                if result.returncode != 0:
                    raise Exception(f"Failed to get connections: {result.stderr}")
                
                lines = result.stdout.strip().split('\n')
                connections = []
                
                if len(lines) > 1:  # First line is the port itself
                    connections = [l.strip() for l in lines[1:] if l.strip()]
                
                return {
                    'success': True,
                    'port': port,
                    'connections': connections,
                    'connection_count': len(connections)
                }
            else:
                # Get all connections
                result = subprocess.run(
                    ["jack_lsp", "-c"],
                    capture_output=True,
                    text=True
                )
                
                if result.returncode != 0:
                    raise Exception(f"Failed to get connections: {result.stderr}")
                
                # Parse connection list
                lines = result.stdout.strip().split('\n')
                all_connections = {}
                current_port = None
                
                for line in lines:
                    if line and not line.startswith('   '):
                        current_port = line
                        all_connections[current_port] = []
                    elif line.strip() and current_port:
                        all_connections[current_port].append(line.strip())
                
                # Filter to only ports with connections
                connected_ports = {k: v for k, v in all_connections.items() if v}
                
                return {
                    'success': True,
                    'total_ports': len(all_connections),
                    'connected_ports': len(connected_ports),
                    'connections': connected_ports
                }
            
        except Exception as e:
            logger.error(f"Failed to get JACK connections: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def connect_system_to_plugin(self, plugin_id: int, 
                                      connect_input: bool = True,
                                      connect_output: bool = False,
                                      session_context: dict = None, **kwargs) -> dict:
        """Connect system audio to/from a plugin
        
        Args:
            plugin_id: Plugin ID in Carla
            connect_input: Connect system capture to plugin input
            connect_output: Connect plugin output to system playback
            
        Returns:
            Connection result
        """
        try:
            if plugin_id not in self.carla.plugins:
                raise Exception(f"Plugin {plugin_id} not found")
            
            plugin_name = self.carla.plugins[plugin_id]['name']
            connections_made = []
            
            # Get plugin info to determine port names
            info = self.carla.host.get_plugin_info(plugin_id)
            
            if connect_input:
                # Find plugin input ports
                plugin_inputs = subprocess.run(
                    ["jack_lsp", "-i"],
                    capture_output=True,
                    text=True
                ).stdout.strip().split('\n')
                
                plugin_inputs = [p for p in plugin_inputs if plugin_name in p]
                
                if plugin_inputs:
                    # Connect system capture to plugin inputs
                    if len(plugin_inputs) >= 2:
                        # Stereo
                        subprocess.run(["jack_connect", "system:capture_1", plugin_inputs[0]])
                        subprocess.run(["jack_connect", "system:capture_2", plugin_inputs[1]])
                        connections_made.append(f"system:capture_1 -> {plugin_inputs[0]}")
                        connections_made.append(f"system:capture_2 -> {plugin_inputs[1]}")
                    else:
                        # Mono
                        subprocess.run(["jack_connect", "system:capture_1", plugin_inputs[0]])
                        connections_made.append(f"system:capture_1 -> {plugin_inputs[0]}")
            
            if connect_output:
                # Find plugin output ports
                plugin_outputs = subprocess.run(
                    ["jack_lsp", "-o"],
                    capture_output=True,
                    text=True
                ).stdout.strip().split('\n')
                
                plugin_outputs = [p for p in plugin_outputs if plugin_name in p and "Audio" in p]
                
                if plugin_outputs:
                    # Connect plugin outputs to system playback
                    if len(plugin_outputs) >= 2:
                        # Stereo
                        subprocess.run(["jack_connect", plugin_outputs[0], "system:playback_1"])
                        subprocess.run(["jack_connect", plugin_outputs[1], "system:playback_2"])
                        connections_made.append(f"{plugin_outputs[0]} -> system:playback_1")
                        connections_made.append(f"{plugin_outputs[1]} -> system:playback_2")
                    else:
                        # Mono to both channels
                        subprocess.run(["jack_connect", plugin_outputs[0], "system:playback_1"])
                        subprocess.run(["jack_connect", plugin_outputs[0], "system:playback_2"])
                        connections_made.append(f"{plugin_outputs[0]} -> system:playback_1")
                        connections_made.append(f"{plugin_outputs[0]} -> system:playback_2")
            
            return {
                'success': True,
                'plugin_id': plugin_id,
                'plugin_name': plugin_name,
                'connections_made': connections_made,
                'connection_count': len(connections_made)
            }
            
        except Exception as e:
            logger.error(f"Failed to connect system to plugin: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def connect_plugin_to_system(self, plugin_id: int,
                                      session_context: dict = None, **kwargs) -> dict:
        """Convenience method to connect plugin output to system playback
        
        Args:
            plugin_id: Plugin ID in Carla
            
        Returns:
            Connection result
        """
        return await self.connect_system_to_plugin(
            plugin_id=plugin_id,
            connect_input=False,
            connect_output=True
        )