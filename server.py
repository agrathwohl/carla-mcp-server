#!/usr/bin/env python3
"""
Carla MCP Server - Complete audio plugin host control via Model Context Protocol
"""

import asyncio
import json
import logging
import sys
import os
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable
from pathlib import Path
import uuid
import numpy as np

# Add Carla to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'source', 'frontend'))

from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from carla_controller import CarlaController
from tools.session_tools import SessionTools
from tools.plugin_tools import PluginTools
from tools.routing_tools import RoutingTools
from tools.parameter_tools import ParameterTools
from tools.analysis_tools import AnalysisTools
from tools.hardware_tools import HardwareTools
from tools.jack_tools import JackTools
from monitors.event_monitor import EventMonitor
from monitors.audio_monitor import AudioMonitor
from monitors.cpu_monitor import CPUMonitor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CarlaMCPServer:
    """Main MCP Server for Carla audio plugin host control"""
    
    def __init__(self, carla_path: str = None):
        """Initialize the Carla MCP Server
        
        Args:
            carla_path: Path to Carla installation (auto-detect if None)
        """
        self.server = Server("carla-mcp")
        
        # Auto-detect Carla path if not provided
        if carla_path is None:
            carla_path = self._find_carla_installation()
        
        # Initialize Carla controller
        self.carla = CarlaController(carla_path)
        
        # Session management
        self.sessions: Dict[str, Any] = {}
        self.active_session_id: Optional[str] = None
        
        # Initialize tool modules
        self.session_tools = SessionTools(self.carla)
        self.plugin_tools = PluginTools(self.carla)
        self.routing_tools = RoutingTools(self.carla)
        self.parameter_tools = ParameterTools(self.carla)
        self.analysis_tools = AnalysisTools(self.carla)
        self.hardware_tools = HardwareTools(self.carla)
        self.jack_tools = JackTools(self.carla)
        
        # Initialize monitors
        self.event_monitor = EventMonitor(self.carla)
        self.audio_monitor = AudioMonitor(self.carla)
        self.cpu_monitor = CPUMonitor(self.carla)
        
        # Performance metrics
        self.metrics = {
            'tool_calls': 0,
            'errors': 0,
            'avg_response_time': 0,
            'active_plugins': 0,
            'cpu_usage': 0
        }
        
        # Register all tools
        self._register_tools()
        
        # Setup event handlers
        self._setup_event_handlers()
        
        logger.info(f"Carla MCP Server initialized with path: {carla_path}")
    
    def _find_carla_installation(self) -> str:
        """Auto-detect Carla installation path"""
        possible_paths = [
            "/home/gwohl/builds/Carla",
            "/usr/share/carla",
            "/usr/local/share/carla",
            os.path.expanduser("~/Carla"),
            "/opt/carla"
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                bin_path = os.path.join(path, "bin", "libcarla_standalone2.so")
                if os.path.exists(bin_path):
                    logger.info(f"Found Carla at: {path}")
                    return path
        
        raise RuntimeError("Could not find Carla installation. Please specify path.")
    
    def _register_tools(self):
        """Register all MCP tools"""
        
        # Session management tools
        @self.server.list_tools()
        async def handle_list_tools() -> list[types.Tool]:
            """Return all available tools"""
            tools = []
            
            # Session tools
            tools.extend([
                types.Tool(
                    name="load_session",
                    description="Load a Carla project/session file",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to .carxp project file"},
                            "auto_connect": {"type": "boolean", "description": "Auto-connect JACK ports", "default": True}
                        },
                        "required": ["path"]
                    }
                ),
                types.Tool(
                    name="save_session",
                    description="Save current session to file",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Save location"},
                            "include_samples": {"type": "boolean", "default": True},
                            "compress": {"type": "boolean", "default": False}
                        },
                        "required": ["path"]
                    }
                ),
                types.Tool(
                    name="create_snapshot",
                    description="Create session snapshot for A/B comparison",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Snapshot name"},
                            "include_audio_files": {"type": "boolean", "default": False}
                        },
                        "required": ["name"]
                    }
                ),
            ])
            
            # Plugin tools
            tools.extend([
                types.Tool(
                    name="load_plugin",
                    description="Load any plugin format (VST2/3, LV2, etc.)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Plugin path or URI"},
                            "type": {"type": "string", "enum": ["VST2", "VST3", "LV2", "LADSPA", "AU"], "description": "Plugin type"},
                            "position": {"type": "integer", "description": "Rack position", "default": -1},
                            "preset": {"type": "string", "description": "Optional preset to load"}
                        },
                        "required": ["path", "type"]
                    }
                ),
                types.Tool(
                    name="list_plugins",
                    description="List all loaded plugins",
                    inputSchema={
                        "type": "object",
                        "properties": {}
                    }
                ),
                types.Tool(
                    name="get_plugin_info",
                    description="Get detailed information about a plugin",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "plugin_id": {"type": "string", "description": "Plugin ID"}
                        },
                        "required": ["plugin_id"]
                    }
                ),
                types.Tool(
                    name="clone_plugin",
                    description="Clone a plugin with its current settings",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "plugin_id": {"type": "string", "description": "Plugin ID to clone"}
                        },
                        "required": ["plugin_id"]
                    }
                ),
                types.Tool(
                    name="replace_plugin",
                    description="Replace a plugin with another while preserving connections",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "plugin_id": {"type": "string", "description": "Plugin ID to replace"},
                            "new_path": {"type": "string", "description": "Path to new plugin"},
                            "new_type": {"type": "string", "description": "New plugin type"},
                            "preserve_connections": {"type": "boolean", "default": True}
                        },
                        "required": ["plugin_id", "new_path", "new_type"]
                    }
                ),
                types.Tool(
                    name="scan_plugins",
                    description="Scan directory for plugins",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "directory": {"type": "string", "description": "Directory to scan"},
                            "formats": {"type": "array", "items": {"type": "string"}, "description": "Plugin types to scan"},
                            "recursive": {"type": "boolean", "default": True}
                        },
                        "required": ["directory"]
                    }
                ),
                types.Tool(
                    name="control_plugin",
                    description="Control plugin state (activate, bypass, solo, remove)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "plugin_id": {"type": "string", "description": "Plugin ID"},
                            "action": {"type": "string", "enum": ["activate", "bypass", "solo", "remove"]},
                            "fade_ms": {"type": "integer", "description": "Fade time in milliseconds", "default": 0}
                        },
                        "required": ["plugin_id", "action"]
                    }
                ),
                types.Tool(
                    name="batch_process",
                    description="Apply plugin chain to audio file",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "input_file": {"type": "string", "description": "Input audio file path"},
                            "plugin_chain": {"type": "array", "items": {"type": "string"}, "description": "Plugin IDs to apply"},
                            "output_format": {
                                "type": "object",
                                "properties": {
                                    "sample_rate": {"type": "integer", "default": 48000},
                                    "bit_depth": {"type": "integer", "default": 24},
                                    "format": {"type": "string", "default": "wav"}
                                }
                            },
                            "normalize": {"type": "boolean", "default": True}
                        },
                        "required": ["input_file", "plugin_chain"]
                    }
                ),
            ])
            
            # Routing tools
            tools.extend([
                types.Tool(
                    name="connect_audio",
                    description="Create audio connections between plugins",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "object",
                                "properties": {
                                    "plugin_id": {"type": "string"},
                                    "port_index": {"type": "integer"}
                                }
                            },
                            "destination": {
                                "type": "object",
                                "properties": {
                                    "plugin_id": {"type": "string"},
                                    "port_index": {"type": "integer"}
                                }
                            },
                            "gain": {"type": "number", "description": "Connection gain in dB", "default": 0}
                        },
                        "required": ["source", "destination"]
                    }
                ),
                types.Tool(
                    name="create_bus",
                    description="Create audio bus for grouping",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Bus name"},
                            "channels": {"type": "integer", "description": "Number of channels (1-8)", "default": 2},
                            "plugins": {"type": "array", "items": {"type": "string"}, "description": "Plugins to route through bus"}
                        },
                        "required": ["name"]
                    }
                ),
                types.Tool(
                    name="setup_sidechain",
                    description="Configure sidechain routing",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "source_plugin": {"type": "string", "description": "Source plugin ID"},
                            "destination_plugin": {"type": "string", "description": "Destination plugin ID"},
                            "sidechain_input": {"type": "integer", "description": "Sidechain input index", "default": 0}
                        },
                        "required": ["source_plugin", "destination_plugin"]
                    }
                ),
                types.Tool(
                    name="get_routing_matrix",
                    description="Get complete routing configuration",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "format": {"type": "string", "enum": ["json", "graphviz", "matrix"], "default": "json"}
                        }
                    }
                ),
            ])
            
            # Parameter tools
            tools.extend([
                types.Tool(
                    name="automate_parameter",
                    description="Create parameter automation",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "plugin_id": {"type": "string"},
                            "parameter_id": {"type": "integer"},
                            "automation_type": {"type": "string", "enum": ["linear", "exponential", "sine", "random_walk"]},
                            "duration_ms": {"type": "integer"},
                            "values": {"type": "array", "items": {"type": "number"}, "description": "Keyframe values"}
                        },
                        "required": ["plugin_id", "parameter_id", "automation_type", "duration_ms"]
                    }
                ),
                types.Tool(
                    name="map_midi_cc",
                    description="Map MIDI CC to parameters",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "plugin_id": {"type": "string"},
                            "parameter_id": {"type": "integer"},
                            "cc_number": {"type": "integer", "description": "MIDI CC number (0-127)"},
                            "channel": {"type": "integer", "description": "MIDI channel (1-16)", "default": 1},
                            "range": {
                                "type": "object",
                                "properties": {
                                    "min": {"type": "number"},
                                    "max": {"type": "number"}
                                }
                            },
                            "curve": {"type": "string", "enum": ["linear", "exponential", "logarithmic"], "default": "linear"}
                        },
                        "required": ["plugin_id", "parameter_id", "cc_number"]
                    }
                ),
                types.Tool(
                    name="create_macro",
                    description="Create macro control for multiple parameters",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "targets": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "plugin_id": {"type": "string"},
                                        "param_id": {"type": "integer"},
                                        "range": {"type": "object"},
                                        "curve": {"type": "string"}
                                    }
                                }
                            }
                        },
                        "required": ["name", "targets"]
                    }
                ),
            ])
            
            # Analysis tools
            tools.extend([
                types.Tool(
                    name="analyze_spectrum",
                    description="Real-time spectrum analysis",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "source": {"type": "string", "description": "Plugin ID or bus ID"},
                            "fft_size": {"type": "integer", "description": "FFT size (512-8192)", "default": 2048},
                            "window": {"type": "string", "enum": ["hann", "blackman", "hamming"], "default": "hann"}
                        },
                        "required": ["source"]
                    }
                ),
                types.Tool(
                    name="measure_levels",
                    description="Get audio levels and statistics",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "window_ms": {"type": "integer", "default": 100},
                            "include_history": {"type": "boolean", "default": False}
                        },
                        "required": ["source"]
                    }
                ),
                types.Tool(
                    name="capture_plugin_parameters",
                    description="Capture all parameter values from one or more plugins over time",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "plugin_ids": {
                                "description": "Single plugin ID or list of plugin IDs"
                            },
                            "capture_duration_ms": {"type": "integer", "default": 10000, "description": "Total capture duration in milliseconds"},
                            "sampling_interval_ms": {"type": "integer", "default": 100, "description": "Time between samples in milliseconds"}
                        },
                        "required": ["plugin_ids"]
                    }
                ),
                types.Tool(
                    name="detect_feedback",
                    description="Detect feedback loops in routing",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "sensitivity": {"type": "number", "description": "Detection sensitivity (0-1)", "default": 0.8}
                        }
                    }
                ),
                types.Tool(
                    name="analyze_latency",
                    description="Measure system and plugin latencies",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "measure_plugins": {"type": "boolean", "default": True},
                            "measure_hardware": {"type": "boolean", "default": True}
                        }
                    }
                ),
            ])
            
            # Hardware tools
            tools.extend([
                types.Tool(
                    name="configure_audio_interface",
                    description="Configure audio hardware settings",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "device": {"type": "string"},
                            "sample_rate": {"type": "integer"},
                            "buffer_size": {"type": "integer"},
                            "channels_in": {"type": "integer"},
                            "channels_out": {"type": "integer"}
                        },
                        "required": ["device"]
                    }
                ),
                types.Tool(
                    name="list_audio_devices",
                    description="List available audio devices",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "driver": {"type": "string", "description": "Audio driver (JACK, ALSA, etc.)"}
                        }
                    }
                ),
            ])
            
            # JACK routing tools
            tools.extend([
                types.Tool(
                    name="list_jack_ports",
                    description="List available JACK ports",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "port_type": {"type": "string", "description": "Filter by type (audio, midi)"},
                            "flags": {"type": "string", "description": "Filter by flags (input, output, physical)"},
                            "name_pattern": {"type": "string", "description": "Filter by name pattern"}
                        }
                    }
                ),
                types.Tool(
                    name="connect_jack_ports",
                    description="Connect two JACK ports",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "source": {"type": "string", "description": "Source port name"},
                            "destination": {"type": "string", "description": "Destination port name"}
                        },
                        "required": ["source", "destination"]
                    }
                ),
                types.Tool(
                    name="disconnect_jack_ports",
                    description="Disconnect two JACK ports",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "source": {"type": "string", "description": "Source port name"},
                            "destination": {"type": "string", "description": "Destination port name"}
                        },
                        "required": ["source", "destination"]
                    }
                ),
                types.Tool(
                    name="get_jack_connections",
                    description="Get connections for a JACK port or all connections",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "port": {"type": "string", "description": "Specific port to check (or None for all)"}
                        }
                    }
                ),
                types.Tool(
                    name="connect_system_to_plugin",
                    description="Connect system audio to/from a plugin",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "plugin_id": {"type": "integer", "description": "Plugin ID in Carla"},
                            "connect_input": {"type": "boolean", "description": "Connect system capture to plugin input", "default": True},
                            "connect_output": {"type": "boolean", "description": "Connect plugin output to system playback", "default": False}
                        },
                        "required": ["plugin_id"]
                    }
                ),
                types.Tool(
                    name="connect_plugin_to_system",
                    description="Connect plugin output to system playback",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "plugin_id": {"type": "integer", "description": "Plugin ID in Carla"}
                        },
                        "required": ["plugin_id"]
                    }
                ),
            ])
            
            return tools
        
        @self.server.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict | None
        ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
            """Handle tool calls"""
            
            start_time = datetime.now()
            self.metrics['tool_calls'] += 1
            
            try:
                result = await self._execute_tool(name, arguments or {})
                
                # Update metrics
                elapsed = (datetime.now() - start_time).total_seconds()
                self.metrics['avg_response_time'] = (
                    (self.metrics['avg_response_time'] * (self.metrics['tool_calls'] - 1) + elapsed) 
                    / self.metrics['tool_calls']
                )
                
                return [types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2)
                )]
                
            except Exception as e:
                self.metrics['errors'] += 1
                logger.error(f"Tool {name} failed: {str(e)}")
                
                return [types.TextContent(
                    type="text",
                    text=json.dumps({
                        "error": str(e),
                        "tool": name,
                        "arguments": arguments
                    }, indent=2)
                )]
    
    async def _execute_tool(self, name: str, arguments: dict) -> dict:
        """Execute a specific tool"""
        
        # Add context to arguments
        arguments['session_context'] = self.get_active_session()
        arguments['performance_metrics'] = self.get_performance_metrics()
        
        # Route to appropriate tool module
        if name.startswith('load_session') or name.startswith('save_') or name.startswith('create_snapshot'):
            return await self.session_tools.execute(name, arguments)
        elif name in ['load_plugin', 'scan_plugins', 'control_plugin', 'batch_process', 'list_plugins', 'get_plugin_info', 'clone_plugin', 'replace_plugin']:
            return await self.plugin_tools.execute(name, arguments)
        elif name in ['connect_audio', 'create_bus', 'setup_sidechain', 'get_routing_matrix']:
            return await self.routing_tools.execute(name, arguments)
        elif name in ['automate_parameter', 'map_midi_cc', 'create_macro']:
            return await self.parameter_tools.execute(name, arguments)
        elif name in ['analyze_spectrum', 'measure_levels', 'capture_plugin_parameters', 'detect_feedback', 'analyze_latency']:
            return await self.analysis_tools.execute(name, arguments)
        elif name in ['configure_audio_interface', 'list_audio_devices']:
            return await self.hardware_tools.execute(name, arguments)
        elif name in ['list_jack_ports', 'connect_jack_ports', 'disconnect_jack_ports', 
                     'get_jack_connections', 'connect_system_to_plugin', 'connect_plugin_to_system']:
            return await self.jack_tools.execute(name, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    
    def _setup_event_handlers(self):
        """Setup Carla event callbacks"""
        
        def on_engine_callback(host, action, plugin_id, value1, value2, value3, valuef, value_str):
            """Handle Carla engine callbacks"""
            # Queue event for async processing
            asyncio.create_task(
                self.event_monitor.handle_event({
                    'action': action,
                    'plugin_id': plugin_id,
                    'value1': value1,
                    'value2': value2,
                    'value3': value3,
                    'valuef': valuef,
                    'value_str': value_str,
                    'timestamp': datetime.now().isoformat()
                })
            )
        
        self.carla.set_callback(on_engine_callback)
    
    def get_active_session(self) -> Optional[dict]:
        """Get active session context"""
        if self.active_session_id:
            return self.sessions.get(self.active_session_id)
        return None
    
    def get_performance_metrics(self) -> dict:
        """Get current performance metrics"""
        return {
            **self.metrics,
            'cpu_usage': self.cpu_monitor.get_current_usage(),
            'active_plugins': self.carla.host.get_current_plugin_count()
        }
    
    async def run(self):
        """Run the MCP server"""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="Carla MCP Server",
                    server_version="1.0.0",
                    capabilities=self.server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )


async def main():
    """Main entry point"""
    # Check for custom Carla path in environment
    carla_path = os.environ.get('CARLA_PATH')
    
    # Create and run server
    server = CarlaMCPServer(carla_path)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())