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
                        "enum": ["VST2", "VST3", "LV2", "LADSPA", "AU", "JACK"],
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

    # Earshot Phase 1 ingestion tool. Single tool, three return shapes
    # (complete | needs_plan | error). See earshot/ingest.py for the contract.
    earshot_tools = [
        ToolDefinition(
            name="earshot_ingest_artist",
            description=(
                "Phase 1 oeuvre ingestion: scrape an artist's public surface "
                "(Bandcamp/SoundCloud built-in, arbitrary sites via LLM-planned "
                "discovery) and produce a structured oeuvre artifact. On unknown "
                "hosts returns status=needs_plan with a DOM summary; the "
                "orchestrator emits a scraping plan and re-invokes with plan=<dict>."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Artist profile URL on any host (Bandcamp, SoundCloud, personal site, etc.)",
                    },
                    "artist_id": {
                        "type": "string",
                        "description": "Optional artist identifier override; defaults to a host-derived slug",
                    },
                    "plan": {
                        "type": "object",
                        "description": (
                            "Orchestrator-supplied scraping plan, required only when continuing "
                            "from a previous needs_plan response for an unknown host. See the "
                            "plan_schema_doc field in that response for the expected shape."
                        ),
                    },
                    "force_rediscover": {
                        "type": "boolean",
                        "description": "Ignore any cached plan and re-run discovery",
                        "default": False,
                    },
                    "force_rerun": {
                        "type": "boolean",
                        "description": "Ignore any cached oeuvre artifact and re-ingest from source",
                        "default": False,
                    },
                },
                "required": ["url"],
            },
            examples=[
                "earshot_ingest_artist(url='https://sonicmultiplicities.bandcamp.com/music')",
                "earshot_ingest_artist(url='https://soundcloud.com/sonicmultiplicities')",
                "earshot_ingest_artist(url='https://multipli.city/', plan={...})",
            ],
        ),
        ToolDefinition(
            name="earshot_analyze_track",
            description=(
                "Phase 2 track pre-analysis: download (if URL) and produce a "
                "structured track-context artifact with tempo / key / LUFS / "
                "LRA / onset rate / spectral centroid / RMS envelope / lyric "
                "transcription. Heavy lifting runs in the Earshot companion "
                "venv (essentia + faster-whisper + librosa). Artifact lives "
                "at ~/.carla-mcp/earshot/tracks/{track_id}/context.json."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "track_url": {
                        "type": "string",
                        "description": "Track URL (any source yt-dlp can fetch) or local file path",
                    },
                    "artist_id": {
                        "type": "string",
                        "description": "Artist identifier the track belongs to (from a prior earshot_ingest_artist call)",
                    },
                    "track_id": {
                        "type": "string",
                        "description": "Optional track identifier; derived from URL tail if absent",
                    },
                    "force_rerun": {
                        "type": "boolean",
                        "description": "Ignore any cached context.json and re-analyze",
                        "default": False,
                    },
                },
                "required": ["track_url", "artist_id"],
            },
            examples=[
                "earshot_analyze_track(track_url='https://sonicmultiplicities.audio/feed/downloads/SM012.flac', artist_id='sonicmultiplicities', track_id='SM012')",
            ],
        ),
        ToolDefinition(
            name="earshot_refresh_expectations",
            description=(
                "Phase G — orchestrator-side prediction refresh. Called in "
                "response to a `boundary_approaching` event in the commentary "
                "queue. Installs per-section predictions in the session's "
                "expectation tracker; the prediction comparator picks them up "
                "immediately. Predictions dict keys are Dimension values like "
                "'tempo', 'dynamic_envelope', 'lufs_integrated'."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Active session id from earshot_start_session",
                    },
                    "section_index": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Which Phase 2 section these predictions apply to",
                    },
                    "predictions": {
                        "type": "object",
                        "description": (
                            "Map of Dimension.value (e.g. 'tempo', 'dynamic_envelope') → "
                            "expected scalar value the agent predicts for this section"
                        ),
                    },
                    "source": {
                        "type": "string",
                        "description": "Diagnostic label, default 'orchestrator'",
                        "default": "orchestrator",
                    },
                },
                "required": ["session_id", "section_index", "predictions"],
            },
            examples=[
                "earshot_refresh_expectations(session_id='abc', section_index=2, predictions={'tempo': 103.5, 'dynamic_envelope': -18.0})",
            ],
        ),
        ToolDefinition(
            name="earshot_submit_commentary",
            description=(
                "Phase I — orchestrator submits LLM-generated prose for an "
                "active session in response to a ProseRequest from the "
                "commentary queue. Content is validated against honesty rules "
                "(anti-spoiler, no marketing language, no feeling claims) "
                "before queueing; rejected submissions return status='rejected' "
                "with reasons so the orchestrator can revise. Use intensity "
                "3-6 for prose; intensity 2 is scheduler-only action-text."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "ts_target_user_clock_ms": {
                        "type": "integer",
                        "description": "When the prose should land for the user (from the ProseRequest's ts_target)",
                    },
                    "intensity": {
                        "type": "integer", "minimum": 3, "maximum": 6,
                        "description": "IntensityLevel value (3=exclamation, 6=reflection)",
                    },
                    "content": {"type": "string", "description": "The prose to deliver"},
                    "source_event_id": {"type": "string", "default": ""},
                    "dimensions": {"type": "array", "items": {"type": "string"}},
                    "score": {"type": "number", "default": 0.0},
                },
                "required": ["session_id", "ts_target_user_clock_ms", "intensity", "content"],
            },
            examples=[
                "earshot_submit_commentary(session_id='abc', ts_target_user_clock_ms=1779200000000, intensity=4, content='the snare just got tight', source_event_id='DriftEvent_1779199995000')",
            ],
        ),
        ToolDefinition(
            name="earshot_get_commentary_queue",
            description=(
                "Phase I — orchestrator drains the session's commentary queue. "
                "Returns CommentaryEmissions whose ts_user_clock_ms has arrived "
                "(ready to deliver to the user) and any pending ProseRequests "
                "(orchestrator must fulfill via earshot_submit_commentary). "
                "Supports long-polling via wait_seconds."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "since_ts_ms": {
                        "type": "integer", "default": 0,
                        "description": "Only return items with target ts >= this (orchestrator's last-seen marker)",
                    },
                    "wait_seconds": {
                        "type": "number", "default": 0.0,
                        "description": "0 = non-blocking; >0 = long-poll up to this many seconds",
                    },
                },
                "required": ["session_id"],
            },
            examples=[
                "earshot_get_commentary_queue(session_id='abc', wait_seconds=2.0)",
            ],
        ),
        ToolDefinition(
            name="earshot_start_session",
            description=(
                "Phase J — bring up a co-listening session for a previously analyzed track. "
                "Loads the Phase 2 baseline, selects a profile, builds the comparator + scheduler "
                "graph, optionally starts an LV2 poller, and registers everything in the session "
                "registry. Returns the session_id used by all subsequent earshot_* tools."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "track_id": {
                        "type": "string",
                        "description": "Track id whose Phase 2 baseline to load (must already exist).",
                    },
                    "mode": {
                        "type": "string", "enum": ["preview", "live"], "default": "preview",
                        "description": "preview = analyzer-only dry run; live = co-listening with delay tower.",
                    },
                    "delay_seconds": {
                        "type": "number", "default": 5.0,
                        "description": "Delay-tower buffer; anti-spoiler timing = ts_ms + delay_seconds*1000.",
                    },
                    "voice_enabled": {
                        "type": "boolean", "default": False,
                        "description": "Whether the orchestrator should render prose via TTS.",
                    },
                    "profile_name": {
                        "type": "string", "default": "experimental",
                        "description": (
                            "Profile YAML stem under earshot/profiles/ "
                            "(experimental|edm|ambient|jazz). Use 'auto' to invoke "
                            "the Phase H selector against the baseline + oeuvre_hint."
                        ),
                    },
                    "oeuvre_hint": {
                        "type": "string",
                        "description": (
                            "Optional genre / aesthetic hint string passed to the "
                            "profile selector when profile_name='auto'. Free-form; "
                            "matched against substrings like 'edm', 'jazz', 'ambient'."
                        ),
                    },
                    "plugin_ids": {
                        "type": "array", "items": {"type": "integer"},
                        "description": "Plugin IDs to poll for ambient-stream entries (requires analysis_tools).",
                    },
                    "semantics": {
                        "type": "object",
                        "description": (
                            "Map of ambient `type` -> Dimension value (tempo, key, dynamic_envelope, "
                            "lufs_integrated, spectral_centroid, onset_density). Required for drift + "
                            "prediction comparators to fire; without it only boundary events reach the "
                            "scheduler."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "alias": {
                        "type": "string",
                        "description": "Explicit session_id; generated if omitted.",
                    },
                    "boundary_lookahead_seconds": {
                        "type": "number", "default": 5.0,
                        "description": "How far ahead of a section boundary the detector fires.",
                    },
                    "poll_interval_ms": {
                        "type": "integer", "default": 250,
                        "description": "LV2 polling cadence in milliseconds (250 = 4 Hz).",
                    },
                    "companion_audio_file": {
                        "type": "string",
                        "description": (
                            "Path to the source audio file for the Phase C streaming "
                            "librosa companion. When supplied, spawns the companion "
                            "subprocess which writes tempo/key/LUFS/onset_density/"
                            "spectral_centroid entries into the ambient stream. "
                            "Required if you want tempo + key drift/prediction tracking "
                            "(the LV2 chain alone covers only dynamics + spectrum)."
                        ),
                    },
                    "companion_chunk_seconds": {
                        "type": "number", "default": 2.0,
                        "description": "Companion analysis window length in seconds.",
                    },
                    "companion_lookahead_seconds": {
                        "type": "number", "default": 8.0,
                        "description": "How far ahead of playback the companion stays.",
                    },
                    "sync_to_audio": {
                        "type": "boolean", "default": True,
                        "description": (
                            "When true (and an LV2 poller is producing semantically-mapped "
                            "data), gate the drift/prediction/boundary components until the "
                            "first non-silent ambient sample arrives. That sample's ts_ms "
                            "becomes playback_start_ms — so the timeline aligns with when "
                            "audio actually starts, not when this tool was called."
                        ),
                    },
                    "sync_monitor_type": {
                        "type": "string",
                        "description": (
                            "Which ambient entry `type` the sync watcher monitors for "
                            "non-silence. Defaults to the first key in `semantics`."
                        ),
                    },
                    "sync_silence_threshold_db": {
                        "type": "number", "default": -65.0,
                        "description": "Values strictly above this count as 'non-silence' (dB-style metrics).",
                    },
                    "sync_timeout_s": {
                        "type": "number", "default": 300.0,
                        "description": "If no non-silence arrives within this many seconds, give up and start the gated components with the original playback_start_ms.",
                    },
                },
                "required": ["track_id"],
            },
            examples=[
                "earshot_start_session(track_id='SM012', mode='preview')",
                "earshot_start_session(track_id='SM012', mode='live', delay_seconds=5.0, plugin_ids=[0,1,2,3], semantics={'plugin_2.param_0': 'dynamic_envelope'})",
                "earshot_start_session(track_id='SM012', mode='live', companion_audio_file='/path/to/SM012.flac', semantics={'tempo_bpm': 'tempo', 'key': 'key', 'lufs_integrated': 'lufs_integrated', 'spectral_centroid': 'spectral_centroid', 'onset_density': 'onset_density'})",
            ],
        ),
        ToolDefinition(
            name="earshot_user_interject",
            description=(
                "Phase J — log a user interjection (comment/correction/question) mid-listen. "
                "Writes a source='user' entry into the session's ambient stream. Durable for "
                "Phase L reflection."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "text": {"type": "string", "description": "What the user said."},
                    "kind": {
                        "type": "string", "default": "comment",
                        "description": "Free-form label: comment | correction | question | ...",
                    },
                },
                "required": ["session_id", "text"],
            },
            examples=[
                "earshot_user_interject(session_id='abc', text='I love this drop', kind='comment')",
            ],
        ),
        ToolDefinition(
            name="earshot_correct_profile",
            description=(
                "Phase J — swap the session's active profile mid-listen. Affects all future "
                "scheduler decisions from the next event onward; already-queued commentary is "
                "not revoked."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "profile_name": {
                        "type": "string",
                        "description": "Profile YAML stem to switch to.",
                    },
                },
                "required": ["session_id", "profile_name"],
            },
            examples=[
                "earshot_correct_profile(session_id='abc', profile_name='experimental')",
            ],
        ),
        ToolDefinition(
            name="earshot_end_session",
            description=(
                "Phase J — tear down a session and return a summary. Stops components in "
                "reverse dependency order (producers first, then scheduler, then comparators, "
                "then writer). The ambient stream JSONL stays on disk for Phase L."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
            examples=[
                "earshot_end_session(session_id='abc')",
            ],
        ),
        ToolDefinition(
            name="earshot_reflect_session",
            description=(
                "Phase L — generate session reflection artifacts. Reads the "
                "session's ambient.jsonl + commentary.jsonl, computes structured "
                "summary stats, and writes reflection_data.json + reflection.md "
                "to the session's directory. The .md is a skeleton; the "
                "orchestrator should append a prose synthesis grounded in the "
                "data file. Works on active OR torn-down sessions (reads disk, "
                "not the in-memory registry)."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session whose artifacts to reflect on.",
                    },
                },
                "required": ["session_id"],
            },
            examples=[
                "earshot_reflect_session(session_id='earshot_SM012_a1b2c3d4')",
            ],
        ),
        ToolDefinition(
            name="earshot_load_analyzer_chain",
            description=(
                "Phase K — bring up the Earshot canonical analyzer chain in Carla. "
                "Loads the saved chain .carxp (default: ~/.carla-mcp/sessions/earshot_analyzer_chain.carxp), "
                "introspects plugins + parameters, returns a plugin map + derived semantics "
                "dict (mapping `plugin_{id}.param_{N}` -> Dimension) suitable for "
                "earshot_start_session. Delay-tower configuration lives in the .carxp; "
                "this tool does not modify delay params."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "carxp_path": {
                        "type": "string",
                        "description": "Override path to the chain .carxp; default uses the canonical saved chain.",
                    },
                    "skip_if_loaded": {
                        "type": "boolean", "default": True,
                        "description": "If true and plugins are already loaded, introspect them without re-loading.",
                    },
                    "include_params": {
                        "type": "boolean", "default": False,
                        "description": (
                            "Include the full per-plugin parameter list. Default False because "
                            "LSP-family plugins (e.g. art_delay_stereo) carry 700+ params and "
                            "would blow up the response. Set True only when you actually need "
                            "to introspect param names/symbols."
                        ),
                    },
                },
                "required": [],
            },
            examples=[
                "earshot_load_analyzer_chain()",
                "earshot_load_analyzer_chain(carxp_path='/path/to/custom_chain.carxp', skip_if_loaded=False)",
                "earshot_load_analyzer_chain(include_params=True)  # full introspection",
            ],
        ),
        ToolDefinition(
            name="earshot_wire_delay_tower",
            description=(
                "Phase K — wire the analyzer chain + delay tower into the live audio path "
                "so the user hears audio ON A DELAY while the analyzer reads raw audio. "
                "Without this, the anti-spoiler delay_buffer_ms is just a timestamp-math "
                "constant — the user actually hears audio in real-time. After this routing, "
                "the user's perception of the audio is delayed by the art_delay_stereo's "
                "configured delay time, and ts_user_clock = event.ts_ms + delay_buffer_ms "
                "becomes physically correct."
            ),
            handler="earshot_tools",
            input_schema={
                "type": "object",
                "properties": {
                    "source_ports": {
                        "type": "array", "items": {"type": "string"},
                        "description": (
                            "Stereo source JACK output ports producing the audio. "
                            "Typical: ['PulseAudio_JACK_Sink:front-left', "
                            "'PulseAudio_JACK_Sink:front-right']."
                        ),
                    },
                    "sink_ports": {
                        "type": "array", "items": {"type": "string"},
                        "description": (
                            "User's audio output ports. Defaults to "
                            "['system:playback_1', 'system:playback_2']."
                        ),
                    },
                    "chain_entry_plugin_id": {
                        "type": "integer", "default": 0,
                        "description": "Plugin index of the analyzer chain's first node.",
                    },
                    "delay_plugin_id": {
                        "type": "integer", "default": 4,
                        "description": "Plugin index of the delay tower (art_delay_stereo).",
                    },
                    "disconnect_source_from_sink": {
                        "type": "boolean", "default": True,
                        "description": (
                            "When true, tear down any direct source -> sink connections so "
                            "the user only hears the delayed path. Set false to keep the "
                            "direct path AND add the delayed path (= echo)."
                        ),
                    },
                },
                "required": ["source_ports"],
            },
            examples=[
                "earshot_wire_delay_tower(source_ports=['PulseAudio_JACK_Sink:front-left', 'PulseAudio_JACK_Sink:front-right'])",
            ],
        ),
    ]


    # Register all tools
    all_tools = (
        session_tools + plugin_tools + routing_tools +
        parameter_tools + analysis_tools + jack_tools + hardware_tools +
        earshot_tools
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