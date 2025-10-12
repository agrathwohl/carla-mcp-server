"""Type definitions for the Carla MCP Server.

This module provides common type hints and data structures used throughout
the Carla MCP Server codebase for improved type safety and documentation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union, Protocol, TypeAlias
from enum import Enum
from dataclasses import dataclass
from datetime import datetime


# Basic type aliases
PluginId = Union[int, str]
ParameterId = int
PortId = int
SessionId = str
SnapshotId = str
JsonDict = Dict[str, Any]
ToolResult = Dict[str, Any]


class PluginType(Enum):
    """Supported plugin types."""
    VST2 = "VST2"
    VST3 = "VST3"
    LV2 = "LV2"
    LADSPA = "LADSPA"
    DSSI = "DSSI"
    AU = "AU"
    SF2 = "SF2"
    SFZ = "SFZ"


class BinaryType(Enum):
    """Binary architecture types."""
    NATIVE = "BINARY_NATIVE"
    WIN32 = "BINARY_WIN32"
    WIN64 = "BINARY_WIN64"
    POSIX32 = "BINARY_POSIX32"
    POSIX64 = "BINARY_POSIX64"


class ProcessMode(Enum):
    """Audio processing modes."""
    SINGLE_CLIENT = "SINGLE_CLIENT"
    MULTIPLE_CLIENTS = "MULTIPLE_CLIENTS"
    CONTINUOUS_RACK = "CONTINUOUS_RACK"
    PATCHBAY = "PATCHBAY"


@dataclass
class PluginInfo:
    """Information about a loaded plugin."""
    id: PluginId
    name: str
    type: PluginType
    binary_type: BinaryType
    audio_ins: int
    audio_outs: int
    midi_ins: int
    midi_outs: int
    parameters: int
    programs: int
    active: bool = False
    volume: float = 1.0
    dry_wet: float = 1.0
    position: int = -1


@dataclass
class ParameterInfo:
    """Information about a plugin parameter."""
    id: ParameterId
    name: str
    symbol: str
    unit: str
    minimum: float
    maximum: float
    default: float
    current: float
    scaled_control_value: Optional[float] = None
    mid_controller: Optional[int] = None


@dataclass
class AudioPortInfo:
    """Information about an audio port."""
    id: PortId
    name: str
    is_input: bool
    group_id: int = 0


@dataclass
class MidiPortInfo:
    """Information about a MIDI port."""
    id: PortId
    name: str
    is_input: bool


@dataclass
class SessionInfo:
    """Information about a session."""
    id: SessionId
    name: str
    path: Optional[str] = None
    created_at: Optional[datetime] = None
    loaded_at: Optional[datetime] = None
    saved_at: Optional[datetime] = None
    plugin_count: int = 0
    plugins: List[PluginInfo] = None
    is_active: bool = False

    def __post_init__(self):
        if self.plugins is None:
            self.plugins = []


@dataclass
class SnapshotInfo:
    """Information about a session snapshot."""
    id: SnapshotId
    name: str
    session_id: SessionId
    created_at: datetime
    path: str
    plugin_states: List[JsonDict]
    include_audio: bool = False


@dataclass
class PerformanceMetrics:
    """Performance monitoring metrics."""
    tool_calls: int = 0
    errors: int = 0
    avg_response_time: float = 0.0
    active_plugins: int = 0
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    audio_dropouts: int = 0


@dataclass
class EngineInfo:
    """Information about the Carla engine."""
    driver_name: str
    sample_rate: float
    buffer_size: int
    running: bool = False
    process_mode: ProcessMode = ProcessMode.SINGLE_CLIENT
    transport_mode: str = "TRANSPORT_MODE_INTERNAL"
    max_parameters: int = 200


class ToolHandler(Protocol):
    """Protocol for tool handler classes."""

    async def execute(self, tool_name: str, arguments: JsonDict) -> ToolResult:
        """Execute a tool with the given arguments.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments

        Returns:
            Tool execution result
        """
        ...


class CarlaController(Protocol):
    """Protocol for Carla controller interface."""

    @property
    def engine_running(self) -> bool:
        """Check if the engine is running."""
        ...

    def start_engine(self) -> bool:
        """Start the Carla engine."""
        ...

    def stop_engine(self) -> bool:
        """Stop the Carla engine."""
        ...

    def load_project(self, path: str) -> bool:
        """Load a Carla project file."""
        ...

    def save_project(self, path: str) -> bool:
        """Save current state to a Carla project file."""
        ...


# Type aliases for common function signatures
ToolExecutor: TypeAlias = "Callable[[str, JsonDict], Awaitable[ToolResult]]"
EventCallback: TypeAlias = "Callable[[JsonDict], None]"
ParameterCallback: TypeAlias = "Callable[[PluginId, ParameterId, float], None]"


class ToolRegistrationError(Exception):
    """Raised when tool registration fails."""
    pass


class SessionError(Exception):
    """Raised when session operations fail."""
    pass


class PluginError(Exception):
    """Raised when plugin operations fail."""
    pass


class EngineError(Exception):
    """Raised when engine operations fail."""
    pass


# Export all public types
__all__ = [
    # Type aliases
    "PluginId",
    "ParameterId",
    "PortId",
    "SessionId",
    "SnapshotId",
    "JsonDict",
    "ToolResult",
    "ToolExecutor",
    "EventCallback",
    "ParameterCallback",

    # Enums
    "PluginType",
    "BinaryType",
    "ProcessMode",

    # Data classes
    "PluginInfo",
    "ParameterInfo",
    "AudioPortInfo",
    "MidiPortInfo",
    "SessionInfo",
    "SnapshotInfo",
    "PerformanceMetrics",
    "EngineInfo",

    # Protocols
    "ToolHandler",
    "CarlaController",

    # Exceptions
    "ToolRegistrationError",
    "SessionError",
    "PluginError",
    "EngineError",
]