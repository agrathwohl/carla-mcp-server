"""Tool registry for Carla MCP Server.

This module provides a clean way to register MCP tools and generate their
schemas, eliminating the massive _register_tools method from the main server.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

from mcp import types

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """Definition of an MCP tool."""
    name: str
    description: str
    handler: str
    input_schema: Dict[str, Any]
    examples: List[str] = field(default_factory=list)
    deprecated: bool = False
    version: str = "1.0.0"


class MCPToolRegistry:
    """Registry for MCP tools with automatic schema generation."""

    def __init__(self):
        """Initialize empty tool registry."""
        self.tools: Dict[str, ToolDefinition] = {}
        self._handler_mapping: Dict[str, str] = {}

    def register_tool(self, tool_def: ToolDefinition) -> None:
        """Register a tool definition.

        Args:
            tool_def: Tool definition to register

        Raises:
            ValueError: If tool name already exists
        """
        if tool_def.name in self.tools:
            raise ValueError(f"Tool '{tool_def.name}' already registered")

        self.tools[tool_def.name] = tool_def
        self._handler_mapping[tool_def.name] = tool_def.handler

        logger.debug(f"Registered tool '{tool_def.name}' for handler '{tool_def.handler}'")

    def get_tool_definitions(self, handler: Optional[str] = None) -> List[ToolDefinition]:
        """Get tool definitions, optionally filtered by handler.

        Args:
            handler: Optional handler name to filter by

        Returns:
            List of tool definitions
        """
        if handler is None:
            return list(self.tools.values())

        return [tool for tool in self.tools.values() if tool.handler == handler]

    def get_mcp_tools(self) -> List[types.Tool]:
        """Convert tool definitions to MCP Tool objects.

        Returns:
            List of MCP Tool objects for the server
        """
        mcp_tools = []

        for tool_def in self.tools.values():
            if tool_def.deprecated:
                continue

            mcp_tool = types.Tool(
                name=tool_def.name,
                description=tool_def.description,
                inputSchema=tool_def.input_schema
            )
            mcp_tools.append(mcp_tool)

        return mcp_tools

    def get_handler_for_tool(self, tool_name: str) -> Optional[str]:
        """Get the handler name for a tool.

        Args:
            tool_name: Tool name to look up

        Returns:
            Handler name or None if not found
        """
        return self._handler_mapping.get(tool_name)

    def get_tool_count(self) -> int:
        """Get total number of registered tools."""
        return len(self.tools)

    def get_handlers(self) -> List[str]:
        """Get list of unique handler names."""
        return list(set(self._handler_mapping.values()))


def create_carla_tool_registry() -> MCPToolRegistry:
    """Create and populate the Carla MCP tool registry.

    Returns:
        Fully populated tool registry
    """
    registry = MCPToolRegistry()

    # Session management tools
    session_tools = [
        ToolDefinition(
            name="load_session",
            description="Load a Carla project/session file",
            handler="session_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to .carxp project file"
                    },
                    "auto_connect": {
                        "type": "boolean",
                        "description": "Auto-connect JACK ports",
                        "default": True
                    }
                },
                "required": ["path"]
            },
            examples=[
                "load_session('./my_project.carxp')",
                "load_session('/home/user/music/session.carxp', auto_connect=False)"
            ]
        ),
        ToolDefinition(
            name="save_session",
            description="Save current session to file",
            handler="session_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Save location"
                    },
                    "include_samples": {
                        "type": "boolean",
                        "default": True
                    },
                    "compress": {
                        "type": "boolean",
                        "default": False
                    }
                },
                "required": ["path"]
            }
        ),
        ToolDefinition(
            name="create_snapshot",
            description="Create session snapshot for A/B comparison",
            handler="session_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Snapshot name"
                    },
                    "include_audio_files": {
                        "type": "boolean",
                        "default": False
                    }
                },
                "required": ["name"]
            }
        ),
        ToolDefinition(
            name="switch_session",
            description="Switch between sessions or snapshots with optional crossfade",
            handler="session_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session or snapshot ID to switch to"
                    },
                    "crossfade_ms": {
                        "type": "integer",
                        "description": "Crossfade duration in milliseconds",
                        "default": 0
                    }
                },
                "required": ["session_id"]
            }
        ),
        ToolDefinition(
            name="list_sessions",
            description="List all available sessions and snapshots",
            handler="session_tools",
            input_schema={
                "type": "object",
                "properties": {}
            }
        ),
        ToolDefinition(
            name="delete_session",
            description="Delete a session or snapshot (cannot delete active session)",
            handler="session_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session or snapshot ID to delete"
                    }
                },
                "required": ["session_id"]
            }
        ),
        ToolDefinition(
            name="export_session",
            description="Export a session to various formats",
            handler="session_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID to export"
                    },
                    "export_path": {
                        "type": "string",
                        "description": "Export destination path"
                    },
                    "format": {
                        "type": "string",
                        "enum": ["carxp", "ardour", "reaper"],
                        "description": "Export format",
                        "default": "carxp"
                    }
                },
                "required": ["session_id", "export_path"]
            }
        ),
        ToolDefinition(
            name="import_session",
            description="Import a session from various formats",
            handler="session_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "import_path": {
                        "type": "string",
                        "description": "Path to import from"
                    },
                    "format": {
                        "type": "string",
                        "enum": ["auto", "carxp", "ardour", "reaper"],
                        "description": "Import format (auto-detect if 'auto')",
                        "default": "auto"
                    }
                },
                "required": ["import_path"]
            }
        ),
    ]

    # Plugin management tools
    plugin_tools = [
        ToolDefinition(
            name="load_plugin",
            description="Load any plugin format (VST2/3, LV2, etc.)",
            handler="plugin_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Plugin path or URI"
                    },
                    "type": {
                        "type": "string",
                        "enum": ["VST2", "VST3", "LV2", "LADSPA", "AU"],
                        "description": "Plugin type"
                    },
                    "position": {
                        "type": "integer",
                        "description": "Rack position",
                        "default": -1
                    },
                    "preset": {
                        "type": "string",
                        "description": "Optional preset to load"
                    }
                },
                "required": ["path", "type"]
            }
        ),
        ToolDefinition(
            name="list_plugins",
            description="List all loaded plugins",
            handler="plugin_tools",
            input_schema={
                "type": "object",
                "properties": {}
            }
        ),
        ToolDefinition(
            name="get_plugin_info",
            description="Get detailed information about a plugin",
            handler="plugin_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {
                        "type": "string",
                        "description": "Plugin ID"
                    }
                },
                "required": ["plugin_id"]
            }
        ),
        ToolDefinition(
            name="control_plugin",
            description="Control plugin state (activate, bypass, solo, remove)",
            handler="plugin_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {
                        "type": "string",
                        "description": "Plugin ID"
                    },
                    "action": {
                        "type": "string",
                        "enum": ["activate", "bypass", "solo", "remove"]
                    },
                    "fade_ms": {
                        "type": "integer",
                        "description": "Fade time in milliseconds",
                        "default": 0
                    }
                },
                "required": ["plugin_id", "action"]
            }
        ),
        ToolDefinition(
            name="scan_plugins",
            description="Scan directory for plugins",
            handler="plugin_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to scan"},
                    "formats": {"type": "array", "items": {"type": "string"}, "description": "Plugin types to scan"},
                    "recursive": {"type": "boolean", "default": True}
                },
                "required": ["directory"]
            }
        ),
        ToolDefinition(
            name="batch_process",
            description="Apply plugin chain to audio file",
            handler="plugin_tools",
            input_schema={
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
        ToolDefinition(
            name="clone_plugin",
            description="Clone a plugin with its current settings",
            handler="plugin_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {"type": "string", "description": "Plugin ID to clone"}
                },
                "required": ["plugin_id"]
            }
        ),
        ToolDefinition(
            name="replace_plugin",
            description="Replace a plugin with another while preserving connections",
            handler="plugin_tools",
            input_schema={
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
    ]

    # Audio routing tools
    routing_tools = [
        ToolDefinition(
            name="connect_audio",
            description="Create audio connections between plugins",
            handler="routing_tools",
            input_schema={
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
                    "gain": {
                        "type": "number",
                        "description": "Connection gain in dB",
                        "default": 0
                    }
                },
                "required": ["source", "destination"]
            }
        ),
        ToolDefinition(
            name="get_routing_matrix",
            description="Get complete routing configuration",
            handler="routing_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["json", "graphviz", "matrix"],
                        "default": "json"
                    }
                }
            }
        ),
        ToolDefinition(
            name="create_bus",
            description="Create audio bus for grouping",
            handler="routing_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Bus name"},
                    "channels": {"type": "integer", "description": "Number of channels (1-8)", "default": 2},
                    "plugins": {"type": "array", "items": {"type": "string"}, "description": "Plugins to route through bus"}
                },
                "required": ["name"]
            }
        ),
        ToolDefinition(
            name="setup_sidechain",
            description="Configure sidechain routing",
            handler="routing_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "source_plugin": {"type": "string", "description": "Source plugin ID"},
                    "destination_plugin": {"type": "string", "description": "Destination plugin ID"},
                    "sidechain_input": {"type": "integer", "description": "Sidechain input index", "default": 0}
                },
                "required": ["source_plugin", "destination_plugin"]
            }
        ),
        ToolDefinition(
            name="disconnect_audio",
            description="Disconnect audio connection",
            handler="routing_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string", "description": "Connection ID to disconnect"}
                },
                "required": ["connection_id"]
            }
        ),
        ToolDefinition(
            name="create_send",
            description="Create send/return effect routing",
            handler="routing_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "source_plugin": {"type": "string", "description": "Source plugin ID"},
                    "send_plugin": {"type": "string", "description": "Send destination plugin ID"},
                    "amount": {"type": "number", "description": "Send amount (0.0 to 1.0)", "default": 0.5},
                    "pre_fader": {"type": "boolean", "description": "Pre-fader send", "default": False}
                },
                "required": ["source_plugin", "send_plugin"]
            }
        ),
        ToolDefinition(
            name="set_connection_gain",
            description="Adjust connection gain level",
            handler="routing_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string", "description": "Connection ID"},
                    "gain": {"type": "number", "description": "Gain in dB"}
                },
                "required": ["connection_id", "gain"]
            }
        ),
    ]

    # Parameter automation tools
    parameter_tools = [
        ToolDefinition(
            name="automate_parameter",
            description="Create parameter automation",
            handler="parameter_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {"type": "string"},
                    "parameter_id": {"type": "integer"},
                    "automation_type": {
                        "type": "string",
                        "enum": ["linear", "exponential", "sine", "random_walk"]
                    },
                    "duration_ms": {"type": "integer"},
                    "values": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Keyframe values"
                    }
                },
                "required": ["plugin_id", "parameter_id", "automation_type", "duration_ms"]
            }
        ),
        ToolDefinition(
            name="map_midi_cc",
            description="Map MIDI CC to parameters",
            handler="parameter_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {"type": "string"},
                    "parameter_id": {"type": "integer"},
                    "cc_number": {
                        "type": "integer",
                        "description": "MIDI CC number (0-127)"
                    },
                    "channel": {
                        "type": "integer",
                        "description": "MIDI channel (1-16)",
                        "default": 1
                    },
                    "range": {
                        "type": "object",
                        "properties": {
                            "min": {"type": "number"},
                            "max": {"type": "number"}
                        }
                    },
                    "curve": {
                        "type": "string",
                        "enum": ["linear", "exponential", "logarithmic"],
                        "default": "linear"
                    }
                },
                "required": ["plugin_id", "parameter_id", "cc_number"]
            }
        ),
        ToolDefinition(
            name="set_parameter",
            description="Set a plugin parameter value",
            handler="parameter_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {
                        "type": "string",
                        "description": "Plugin ID"
                    },
                    "parameter_id": {
                        "type": "integer",
                        "description": "Parameter index"
                    },
                    "value": {
                        "type": "number",
                        "description": "Parameter value (typically 0.0 to 1.0)"
                    },
                    "session_context": {
                        "type": "object",
                        "description": "Optional session context data"
                    }
                },
                "required": ["plugin_id", "parameter_id", "value"],
                "additionalProperties": False
            }
        ),
        ToolDefinition(
            name="get_parameter",
            description="Get a plugin parameter value and information",
            handler="parameter_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {
                        "type": "string",
                        "description": "Plugin ID"
                    },
                    "parameter_id": {
                        "type": "integer",
                        "description": "Parameter index"
                    }
                },
                "required": ["plugin_id", "parameter_id"]
            }
        ),
        ToolDefinition(
            name="create_macro",
            description="Create macro control for multiple parameters",
            handler="parameter_tools",
            input_schema={
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
        ToolDefinition(
            name="record_automation",
            description="Record parameter automation in real-time",
            handler="parameter_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {"type": "string", "description": "Plugin ID"},
                    "parameters": {"type": "array", "items": {"type": "integer"}, "description": "Parameter IDs to record"},
                    "duration_ms": {"type": "integer", "description": "Recording duration in milliseconds"},
                    "quantize": {"type": "boolean", "description": "Quantize to tempo", "default": False}
                },
                "required": ["plugin_id", "parameters", "duration_ms"]
            }
        ),
        ToolDefinition(
            name="randomize_parameters",
            description="Randomly adjust plugin parameters for creative exploration",
            handler="parameter_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {"type": "string", "description": "Plugin ID"},
                    "amount": {"type": "number", "description": "Randomization amount (0.0 to 1.0)", "default": 0.5},
                    "exclude_parameters": {"type": "array", "items": {"type": "integer"}, "description": "Parameters to exclude from randomization"}
                },
                "required": ["plugin_id"]
            }
        ),
        ToolDefinition(
            name="morph_parameters",
            description="Smoothly morph between parameter states",
            handler="parameter_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {"type": "string", "description": "Plugin ID"},
                    "target_state": {"type": "object", "description": "Target parameter values"},
                    "duration_ms": {"type": "integer", "description": "Morph duration in milliseconds", "default": 1000},
                    "curve": {"type": "string", "enum": ["linear", "exponential", "sine"], "default": "linear"}
                },
                "required": ["plugin_id", "target_state"]
            }
        ),
    ]

    # Analysis tools
    analysis_tools = [
        ToolDefinition(
            name="analyze_spectrum",
            description="Real-time spectrum analysis",
            handler="analysis_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Plugin ID or bus ID"
                    },
                    "fft_size": {
                        "type": "integer",
                        "description": "FFT size (512-8192)",
                        "default": 2048
                    },
                    "window": {
                        "type": "string",
                        "enum": ["hann", "blackman", "hamming"],
                        "default": "hann"
                    }
                },
                "required": ["source"]
            }
        ),
        ToolDefinition(
            name="measure_levels",
            description="Get audio levels and statistics",
            handler="analysis_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "window_ms": {
                        "type": "integer",
                        "default": 100
                    },
                    "include_history": {
                        "type": "boolean",
                        "default": False
                    }
                },
                "required": ["source"]
            }
        ),
        ToolDefinition(
            name="capture_plugin_parameters",
            description="Capture all parameter values from one or more plugins over time",
            handler="analysis_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_ids": {
                        "description": "Single plugin ID or list of plugin IDs"
                    },
                    "capture_duration_ms": {
                        "type": "integer",
                        "default": 10000,
                        "description": "Total capture duration in milliseconds"
                    },
                    "sampling_interval_ms": {
                        "type": "integer",
                        "default": 100,
                        "description": "Time between samples in milliseconds"
                    }
                },
                "required": ["plugin_ids"]
            }
        ),
        ToolDefinition(
            name="detect_feedback",
            description="Detect feedback loops in routing",
            handler="analysis_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "sensitivity": {"type": "number", "description": "Detection sensitivity (0-1)", "default": 0.8}
                }
            }
        ),
        ToolDefinition(
            name="analyze_latency",
            description="Measure system and plugin latencies",
            handler="analysis_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "measure_plugins": {"type": "boolean", "default": True},
                    "measure_hardware": {"type": "boolean", "default": True}
                }
            }
        ),
    ]

    # JACK routing tools
    jack_tools = [
        ToolDefinition(
            name="list_jack_ports",
            description="List available JACK ports",
            handler="jack_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "port_type": {
                        "type": "string",
                        "description": "Filter by type (audio, midi)"
                    },
                    "flags": {
                        "type": "string",
                        "description": "Filter by flags (input, output, physical)"
                    },
                    "name_pattern": {
                        "type": "string",
                        "description": "Filter by name pattern"
                    }
                }
            }
        ),
        ToolDefinition(
            name="connect_jack_ports",
            description="Connect two JACK ports",
            handler="jack_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Source port name"
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination port name"
                    }
                },
                "required": ["source", "destination"]
            }
        ),
        ToolDefinition(
            name="disconnect_jack_ports",
            description="Disconnect two JACK ports",
            handler="jack_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Source port name"
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination port name"
                    }
                },
                "required": ["source", "destination"]
            }
        ),
        ToolDefinition(
            name="get_jack_connections",
            description="Get connections for a JACK port or all connections",
            handler="jack_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "port": {"type": "string", "description": "Specific port to check (or None for all)"}
                }
            }
        ),
        ToolDefinition(
            name="connect_system_to_plugin",
            description="Connect system audio to/from a plugin",
            handler="jack_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {"type": "integer", "description": "Plugin ID in Carla"},
                    "connect_input": {"type": "boolean", "description": "Connect system capture to plugin input", "default": True},
                    "connect_output": {"type": "boolean", "description": "Connect plugin output to system playback", "default": False}
                },
                "required": ["plugin_id"]
            }
        ),
        ToolDefinition(
            name="connect_plugin_to_system",
            description="Connect plugin output to system playback",
            handler="jack_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "plugin_id": {"type": "integer", "description": "Plugin ID in Carla"}
                },
                "required": ["plugin_id"]
            }
        ),
    ]

    # Hardware interface tools
    hardware_tools = [
        ToolDefinition(
            name="configure_audio_interface",
            description="Configure audio hardware settings",
            handler="hardware_tools",
            input_schema={
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
        ToolDefinition(
            name="list_audio_devices",
            description="List available audio devices",
            handler="hardware_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "driver": {
                        "type": "string",
                        "description": "Audio driver (JACK, ALSA, etc.)"
                    }
                }
            }
        ),
        ToolDefinition(
            name="map_control_surface",
            description="Configure MIDI control surface mapping",
            handler="hardware_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "Control surface device name"},
                    "mapping_preset": {"type": "string", "description": "Optional preset name"},
                    "custom_mapping": {
                        "type": "object",
                        "description": "Custom control mappings",
                        "properties": {
                            "cc_mappings": {"type": "array"},
                            "button_mappings": {"type": "array"},
                            "fader_mappings": {"type": "array"}
                        }
                    }
                },
                "required": ["device_name"]
            }
        ),
    ]

    # Register all tools
    all_tools = (
        session_tools + plugin_tools + routing_tools +
        parameter_tools + analysis_tools + jack_tools + hardware_tools
    )

    for tool in all_tools:
        registry.register_tool(tool)

    logger.info(f"Registered {registry.get_tool_count()} tools across {len(registry.get_handlers())} handlers")
    return registry


# Export public interface
__all__ = [
    "ToolDefinition",
    "MCPToolRegistry",
    "create_carla_tool_registry",
]