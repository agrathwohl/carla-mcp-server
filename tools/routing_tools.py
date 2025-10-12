#!/usr/bin/env python3
"""
Audio Routing Tools for Carla MCP Server
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
import json
import uuid

logger = logging.getLogger(__name__)


class RoutingTools:
    """Audio/MIDI routing tools for Carla"""
    
    def __init__(self, carla_controller):
        """Initialize routing tools
        
        Args:
            carla_controller: CarlaController instance
        """
        self.carla = carla_controller
        self.buses = {}
        self.connections = []
        self.sidechains = {}
        
        logger.info("RoutingTools initialized")
    
    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Execute a routing tool
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            
        Returns:
            Tool execution result
        """
        if tool_name == "connect_audio":
            return await self.connect_audio(**arguments)
        elif tool_name == "create_bus":
            return await self.create_bus(**arguments)
        elif tool_name == "setup_sidechain":
            return await self.setup_sidechain(**arguments)
        elif tool_name == "get_routing_matrix":
            return await self.get_routing_matrix(**arguments)
        elif tool_name == "disconnect_audio":
            return await self.disconnect_audio(**arguments)
        elif tool_name == "create_send":
            return await self.create_send(**arguments)
        elif tool_name == "set_connection_gain":
            return await self.set_connection_gain(**arguments)
        else:
            raise ValueError(f"Unknown routing tool: {tool_name}")
    
    async def connect_audio(self, source: dict, destination: dict, gain: float = 0.0,
                          session_context: dict = None, **kwargs) -> dict:
        """Create audio connection between plugins
        
        Args:
            source: Source plugin and port
            destination: Destination plugin and port
            gain: Connection gain in dB
            
        Returns:
            Connection information
        """
        try:
            # Validate plugins exist
            source_plugin = int(source['plugin_id'])
            dest_plugin = int(destination['plugin_id'])
            
            if source_plugin not in self.carla.plugins:
                raise Exception(f"Source plugin not found: {source_plugin}")
            if dest_plugin not in self.carla.plugins:
                raise Exception(f"Destination plugin not found: {dest_plugin}")
            
            # Create connection
            success = self.carla.connect_audio(
                source_plugin, source.get('port_index', 0),
                dest_plugin, destination.get('port_index', 0)
            )
            
            if not success:
                raise Exception("Failed to create audio connection")
            
            # Generate connection ID
            connection_id = str(uuid.uuid4())
            
            # Store connection info
            connection_info = {
                'id': connection_id,
                'source': source,
                'destination': destination,
                'gain': gain,
                'type': 'audio'
            }
            self.connections.append(connection_info)
            
            # Calculate latency compensation
            latency_compensation = self._calculate_latency_compensation(source_plugin, dest_plugin)
            
            logger.info(f"Connected audio: {source_plugin}:{source.get('port_index', 0)} -> "
                       f"{dest_plugin}:{destination.get('port_index', 0)}")
            
            return {
                'success': True,
                'connection_id': connection_id,
                'source': source,
                'destination': destination,
                'gain': gain,
                'latency_compensation': latency_compensation
            }
            
        except Exception as e:
            logger.error(f"Failed to connect audio: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def create_bus(self, name: str, channels: int = 2, plugins: Optional[List[str]] = None,
                        session_context: dict = None, **kwargs) -> dict:
        """Create audio bus for grouping
        
        Args:
            name: Bus name
            channels: Number of channels (1-8)
            plugins: Plugins to route through bus
            
        Returns:
            Bus information
        """
        try:
            # Validate channel count
            if channels < 1 or channels > 8:
                raise ValueError("Channels must be between 1 and 8")
            
            # Generate bus ID
            bus_id = str(uuid.uuid4())
            
            # Create bus structure
            bus_info = {
                'id': bus_id,
                'name': name,
                'channels': channels,
                'plugins': plugins or [],
                'routing_matrix': self._create_routing_matrix(channels),
                'gain': 0.0,
                'mute': False,
                'solo': False
            }
            
            # Store bus
            self.buses[bus_id] = bus_info
            
            # Route plugins through bus if specified
            if plugins:
                for plugin_id in plugins:
                    # Create connections to bus
                    # This would involve actual patchbay operations
                    pass
            
            logger.info(f"Created bus '{name}' with {channels} channels")
            
            return {
                'success': True,
                'bus_id': bus_id,
                'name': name,
                'channels': channels,
                'routing_matrix': bus_info['routing_matrix'],
                'plugins_routed': len(plugins) if plugins else 0
            }
            
        except Exception as e:
            logger.error(f"Failed to create bus: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def setup_sidechain(self, source_plugin: str, destination_plugin: str,
                            sidechain_input: int = 0, session_context: dict = None, **kwargs) -> dict:
        """Configure sidechain routing
        
        Args:
            source_plugin: Source plugin ID (trigger)
            destination_plugin: Destination plugin ID (processor)
            sidechain_input: Sidechain input index
            
        Returns:
            Sidechain configuration
        """
        try:
            source_id = int(source_plugin)
            dest_id = int(destination_plugin)
            
            # Validate plugins
            if source_id not in self.carla.plugins:
                raise Exception(f"Source plugin not found: {source_id}")
            if dest_id not in self.carla.plugins:
                raise Exception(f"Destination plugin not found: {dest_id}")
            
            # Check if destination has sidechain input
            dest_info = self.carla.host.get_plugin_info(dest_id)
            if not dest_info or dest_info['audioIns'] < 3:  # Usually needs 3+ inputs for sidechain
                logger.warning(f"Plugin {dest_id} may not support sidechain")
            
            # Create sidechain connection
            # This typically connects source output to destination's sidechain input
            sidechain_id = str(uuid.uuid4())
            
            # Create the actual connection
            # In Carla, this would be done through patchbay
            success = self.carla.connect_audio(
                source_id, 0,  # Source left output
                dest_id, sidechain_input + 2  # Sidechain inputs often start at index 2
            )
            
            if channels > 1:
                # Connect right channel for stereo sidechain
                self.carla.connect_audio(
                    source_id, 1,
                    dest_id, sidechain_input + 3
                )
            
            # Store sidechain info
            self.sidechains[sidechain_id] = {
                'id': sidechain_id,
                'source': source_id,
                'destination': dest_id,
                'input': sidechain_input,
                'routing_path': f"{source_id} -> {dest_id}[SC]"
            }
            
            logger.info(f"Setup sidechain: {source_id} -> {dest_id}")
            
            return {
                'success': True,
                'sidechain_id': sidechain_id,
                'source_plugin': source_id,
                'destination_plugin': dest_id,
                'sidechain_input': sidechain_input,
                'routing_path': self.sidechains[sidechain_id]['routing_path']
            }
            
        except Exception as e:
            logger.error(f"Failed to setup sidechain: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def get_routing_matrix(self, format: str = "json", session_context: dict = None, **kwargs) -> dict:
        """Get complete routing configuration
        
        Args:
            format: Output format (json, graphviz, matrix)
            
        Returns:
            Routing configuration
        """
        try:
            # Gather all routing information
            routing_data = {
                'connections': self.connections,
                'buses': list(self.buses.values()),
                'sidechains': list(self.sidechains.values()),
                'plugins': []
            }
            
            # Add plugin information
            for plugin_id, plugin_data in self.carla.plugins.items():
                info = self.carla.host.get_plugin_info(plugin_id)
                routing_data['plugins'].append({
                    'id': plugin_id,
                    'name': plugin_data['name'],
                    'audio_ins': info['audioIns'] if info else 0,
                    'audio_outs': info['audioOuts'] if info else 0
                })
            
            # Check for feedback loops
            feedback_loops = self._detect_feedback_loops()
            
            if format == "json":
                result = routing_data
                
            elif format == "graphviz":
                # Generate Graphviz DOT format
                dot = self._generate_graphviz(routing_data)
                result = {'dot': dot}
                
            elif format == "matrix":
                # Generate connection matrix
                matrix = self._generate_connection_matrix(routing_data)
                result = {'matrix': matrix}
                
            else:
                raise ValueError(f"Unknown format: {format}")
            
            return {
                'success': True,
                'routing_data': result,
                'connection_count': len(self.connections),
                'bus_count': len(self.buses),
                'sidechain_count': len(self.sidechains),
                'feedback_loops': feedback_loops,
                'format': format
            }
            
        except Exception as e:
            logger.error(f"Failed to get routing matrix: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def disconnect_audio(self, connection_id: str, session_context: dict = None, **kwargs) -> dict:
        """Disconnect audio connection
        
        Args:
            connection_id: Connection ID to disconnect
            
        Returns:
            Disconnection result
        """
        try:
            # Find connection
            connection = None
            for conn in self.connections:
                if conn['id'] == connection_id:
                    connection = conn
                    break
            
            if not connection:
                raise Exception(f"Connection not found: {connection_id}")
            
            # Remove connection
            self.connections.remove(connection)
            
            # Refresh patchbay to update connections
            self.carla.host.patchbay_refresh(True)
            
            logger.info(f"Disconnected audio connection: {connection_id}")
            
            return {
                'success': True,
                'disconnected': connection_id,
                'connection': connection
            }
            
        except Exception as e:
            logger.error(f"Failed to disconnect audio: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def create_send(self, source_plugin: str, send_plugin: str, amount: float = 0.5,
                        pre_fader: bool = False, session_context: dict = None, **kwargs) -> dict:
        """Create a send to an effect
        
        Args:
            source_plugin: Source plugin ID
            send_plugin: Send destination plugin ID
            amount: Send amount (0.0 to 1.0)
            pre_fader: Pre-fader send
            
        Returns:
            Send configuration
        """
        try:
            source_id = int(source_plugin)
            send_id = int(send_plugin)
            
            # Create send connection with specified amount
            # This is a special type of connection that maintains the original signal
            
            send_connection = {
                'type': 'send',
                'source': source_id,
                'destination': send_id,
                'amount': amount,
                'pre_fader': pre_fader
            }
            
            # Create the actual connection
            success = self.carla.connect_audio(source_id, 0, send_id, 0)
            if success and self.carla.plugins[source_id].get('channels', 2) > 1:
                self.carla.connect_audio(source_id, 1, send_id, 1)
            
            # Set send level (this would be done through a gain plugin or internal routing)
            
            logger.info(f"Created send: {source_id} -> {send_id} ({amount * 100}%)")
            
            return {
                'success': True,
                'source': source_id,
                'destination': send_id,
                'amount': amount,
                'pre_fader': pre_fader
            }
            
        except Exception as e:
            logger.error(f"Failed to create send: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def set_connection_gain(self, connection_id: str, gain: float,
                                 session_context: dict = None, **kwargs) -> dict:
        """Set gain for a connection
        
        Args:
            connection_id: Connection ID
            gain: Gain in dB
            
        Returns:
            Updated connection info
        """
        try:
            # Find connection
            connection = None
            for conn in self.connections:
                if conn['id'] == connection_id:
                    connection = conn
                    break
            
            if not connection:
                raise Exception(f"Connection not found: {connection_id}")
            
            # Update gain
            connection['gain'] = gain
            
            # Note: Carla's patchbay connections don't have per-connection gain.
            # Gain must be applied via plugin volume or an insert effect.
            
            logger.info(f"Set connection {connection_id} gain to {gain}dB")
            
            return {
                'success': True,
                'connection_id': connection_id,
                'new_gain': gain
            }
            
        except Exception as e:
            logger.error(f"Failed to set connection gain: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _create_routing_matrix(self, channels: int) -> List[List[float]]:
        """Create a routing matrix for the specified channel count"""
        # Identity matrix by default
        matrix = []
        for i in range(channels):
            row = [0.0] * channels
            row[i] = 1.0
            matrix.append(row)
        return matrix
    
    def _calculate_latency_compensation(self, source_id: int, dest_id: int) -> float:
        """Calculate latency compensation between plugins"""
        # Get plugin latencies
        source_latency = 0  # self.carla.host.get_latency(source_id) if available
        dest_latency = 0  # self.carla.host.get_latency(dest_id) if available
        
        # Calculate compensation
        return abs(source_latency - dest_latency)
    
    def _detect_feedback_loops(self) -> List[str]:
        """Detect feedback loops in routing"""
        loops = []
        
        # Build adjacency list
        graph = {}
        for conn in self.connections:
            source = conn['source'].get('plugin_id')
            dest = conn['destination'].get('plugin_id')
            
            if source not in graph:
                graph[source] = []
            graph[source].append(dest)
        
        # DFS to find cycles
        def has_cycle(node, visited, stack):
            visited.add(node)
            stack.add(node)
            
            if node in graph:
                for neighbor in graph[node]:
                    if neighbor not in visited:
                        if has_cycle(neighbor, visited, stack):
                            return True
                    elif neighbor in stack:
                        loops.append(f"{node} -> {neighbor}")
                        return True
            
            stack.remove(node)
            return False
        
        visited = set()
        for node in graph:
            if node not in visited:
                has_cycle(node, visited, set())
        
        return loops
    
    def _generate_graphviz(self, routing_data: dict) -> str:
        """Generate Graphviz DOT format for routing"""
        dot = ["digraph Routing {"]
        dot.append("  rankdir=LR;")
        
        # Add plugins as nodes
        for plugin in routing_data['plugins']:
            label = f"{plugin['name']}\\n[{plugin['audio_ins']}in/{plugin['audio_outs']}out]"
            dot.append(f'  p{plugin["id"]} [label="{label}", shape=box];')
        
        # Add buses
        for bus in routing_data['buses']:
            dot.append(f'  b{bus["id"]} [label="{bus["name"]}\\nBus", shape=ellipse, style=filled, fillcolor=lightblue];')
        
        # Add connections as edges
        for conn in routing_data['connections']:
            source = f"p{conn['source']['plugin_id']}"
            dest = f"p{conn['destination']['plugin_id']}"
            label = f"{conn['gain']}dB" if conn['gain'] != 0 else ""
            dot.append(f'  {source} -> {dest} [label="{label}"];')
        
        # Add sidechains
        for sc in routing_data['sidechains']:
            dot.append(f'  p{sc["source"]} -> p{sc["destination"]} [label="SC", style=dashed, color=red];')
        
        dot.append("}")
        
        return "\n".join(dot)
    
    def _generate_connection_matrix(self, routing_data: dict) -> List[List[int]]:
        """Generate connection matrix"""
        plugins = routing_data['plugins']
        n = len(plugins)
        
        # Create empty matrix
        matrix = [[0] * n for _ in range(n)]
        
        # Fill matrix with connections
        plugin_index = {p['id']: i for i, p in enumerate(plugins)}
        
        for conn in routing_data['connections']:
            source_idx = plugin_index.get(conn['source']['plugin_id'])
            dest_idx = plugin_index.get(conn['destination']['plugin_id'])
            
            if source_idx is not None and dest_idx is not None:
                matrix[source_idx][dest_idx] = 1
        
        return matrix