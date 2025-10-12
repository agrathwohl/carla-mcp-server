# Carla MCP Server Architecture

Comprehensive architectural documentation for the Carla MCP Server project.

## Project Overview

The Carla MCP Server is a production-ready Model Context Protocol (MCP) server that provides comprehensive control over the Carla audio plugin host. Built with Python 3.12+, it offers 45+ tools across 7 functional categories for professional audio production workflows.

**Key Metrics:**
- **Codebase Size**: ~6,500 lines of Python code
- **Tool Count**: 45+ implemented methods across 7 categories
- **Test Coverage**: 660+ lines of comprehensive test suite
- **Architecture**: Modular design with clear separation of concerns

## Directory Structure

```
carla-mcp-server/
├── 📁 config/                     # Configuration files
│   └── config.yaml                # Server configuration
├── 📁 docs/                       # Documentation
├── 📁 monitors/                   # Real-time monitoring (310 lines)
│   ├── audio_monitor.py           # Audio level monitoring (108 lines)
│   ├── cpu_monitor.py             # Performance monitoring (128 lines)
│   └── event_monitor.py           # Event streaming (74 lines)
├── 📁 tests/                      # Test suite (660+ lines)
│   ├── test_complete_suite.py     # Comprehensive integration tests (468 lines)
│   └── test_server.py             # Unit tests (192 lines)
├── 📁 tools/                      # MCP tool implementations (2,900+ lines)
│   ├── analysis_tools.py          # Real-time analysis (534 lines)
│   ├── hardware_tools.py          # Hardware interface (235 lines)
│   ├── jack_tools.py              # JACK integration (394 lines)
│   ├── parameter_tools.py         # Parameter automation (740 lines)
│   ├── plugin_tools.py            # Plugin control (689 lines)
│   ├── routing_tools.py           # Audio routing (558 lines)
│   └── session_tools.py           # Session management (567 lines)
├── 📄 base_tools.py               # Base tool framework (355 lines)
├── 📄 carla_controller.py         # Carla backend wrapper (800 lines)
├── 📄 main.py                     # Entry point (6 lines)
├── 📄 server.py                   # Main MCP server (701 lines)
├── 📄 tool_registry.py            # Tool registration system (569 lines)
├── 📄 types.py                    # Type definitions (263 lines)
├── 📄 pyproject.toml              # Python project configuration
├── 📄 requirements.txt            # Dependencies
├── 📄 .mcp.json                   # MCP client configuration
└── 📄 README.md                   # Main documentation
```

## Core Architecture Components

### 1. MCP Server Core (`server.py` - 701 lines)

The main MCP server implementation that orchestrates all functionality.

**Key Classes:**
- `CarlaMCPServer`: Main server class with tool routing and lifecycle management
- Tool registration and execution framework
- WebSocket/stdio communication handling
- Error handling and logging

**Responsibilities:**
- MCP protocol implementation
- Tool discovery and registration
- Request routing and response handling
- Session lifecycle management
- Performance monitoring integration

### 2. Carla Controller (`carla_controller.py` - 800 lines)

High-level wrapper for Carla backend operations, providing a clean Python API.

**Key Classes:**
- `CarlaController`: Main interface to Carla engine
- Plugin type enumerations (`PluginType`, `BinaryType`)
- Engine state management
- Audio/MIDI port handling

**Responsibilities:**
- Carla engine lifecycle (start/stop)
- Plugin loading and management
- Audio routing and connections
- Parameter control and automation
- Project file operations

### 3. Tool Registry System (`tool_registry.py` - 569 lines)

Clean registration system for MCP tools with automatic schema generation.

**Key Classes:**
- `ToolDefinition`: Structured tool metadata
- `MCPToolRegistry`: Registry with schema generation
- `create_carla_tool_registry()`: Factory function

**Features:**
- Automatic MCP tool schema generation
- Tool categorization and versioning
- Handler mapping and resolution
- Deprecation support

### 4. Type System (`types.py` - 263 lines)

Comprehensive type definitions for type safety and documentation.

**Key Components:**
- Type aliases: `PluginId`, `ParameterId`, `JsonDict`, etc.
- Enums: `PluginType`, `BinaryType`, `ProcessMode`
- Data classes: `PluginInfo`, `SessionInfo`, `PerformanceMetrics`
- Protocols: `ToolHandler`, `CarlaController`
- Custom exceptions: `PluginError`, `SessionError`, etc.

### 5. Base Tool Framework (`base_tools.py` - 355 lines)

Foundation framework for all tool implementations.

**Key Features:**
- Abstract base classes for tool handlers
- Common error handling patterns
- Validation utilities
- Response formatting standards

## Tool Implementation Architecture

### Tool Categories and Implementation

Each tool category is implemented as a separate module with consistent patterns:

#### 1. Session Management (`session_tools.py` - 567 lines)
- **Methods**: 8 (load_session, save_session, create_snapshot, switch_session, etc.)
- **Focus**: Project lifecycle and state management
- **Dependencies**: File I/O, Carla project format

#### 2. Plugin Control (`plugin_tools.py` - 689 lines)
- **Methods**: 8 (load_plugin, scan_plugins, control_plugin, batch_process, etc.)
- **Focus**: Plugin loading, control, and processing
- **Dependencies**: Carla plugin API, file system scanning

#### 3. Audio Routing (`routing_tools.py` - 558 lines)
- **Methods**: 8 (connect_audio, create_bus, setup_sidechain, etc.)
- **Focus**: Audio connections and routing matrix
- **Dependencies**: Carla patchbay, JACK integration

#### 4. Parameter Automation (`parameter_tools.py` - 740 lines)
- **Methods**: 8 (automate_parameter, map_midi_cc, create_macro, etc.)
- **Focus**: Parameter control and automation
- **Dependencies**: MIDI handling, automation curves

#### 5. Real-Time Analysis (`analysis_tools.py` - 534 lines)
- **Methods**: 5 (analyze_spectrum, measure_levels, detect_feedback, etc.)
- **Focus**: Audio analysis and measurement
- **Dependencies**: NumPy, SciPy for signal processing

#### 6. JACK Integration (`jack_tools.py` - 394 lines)
- **Methods**: 6 (list_jack_ports, connect_jack_ports, etc.)
- **Focus**: JACK audio system integration
- **Dependencies**: JACK client library

#### 7. Hardware Control (`hardware_tools.py` - 235 lines)
- **Methods**: 3+ (configure_audio_interface, list_audio_devices, etc.)
- **Focus**: Audio hardware configuration
- **Dependencies**: System audio drivers

### Tool Implementation Pattern

All tool classes follow a consistent pattern:

```python
class ToolClass:
    def __init__(self, carla_controller):
        self.carla = carla_controller

    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Main execution entry point"""
        # Route to specific method based on tool_name

    async def specific_tool_method(self, param1: str, param2: int, **kwargs) -> dict:
        """Individual tool implementation"""
        try:
            # 1. Validate parameters
            # 2. Execute operation via carla_controller
            # 3. Format response
            return {"success": True, "data": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
```

## Monitoring Architecture

### Real-Time Monitoring System

The monitoring system provides real-time insights into system performance and audio metrics:

#### 1. Audio Monitor (`audio_monitor.py` - 108 lines)
- Level metering (Peak, RMS, LUFS)
- Spectrum analysis
- Audio dropout detection
- Signal quality metrics

#### 2. CPU Monitor (`cpu_monitor.py` - 128 lines)
- System CPU usage tracking
- Plugin-specific CPU monitoring
- Memory usage statistics
- Performance bottleneck detection

#### 3. Event Monitor (`event_monitor.py` - 74 lines)
- Real-time event streaming
- MIDI event capture
- Parameter change tracking
- Session state notifications

## Configuration Architecture

### Configuration Hierarchy

1. **Environment Variables**
   - `CARLA_PATH`: Carla installation path
   - `PYTHONPATH`: Python module resolution
   - `LD_LIBRARY_PATH`: Shared library resolution

2. **MCP Configuration** (`.mcp.json`)
   - MCP client connection settings
   - Server execution parameters
   - Environment variable definitions

3. **Application Configuration** (`config/config.yaml`)
   - Server host/port settings
   - Carla-specific configuration
   - Audio driver preferences
   - Plugin scan paths

4. **Project Configuration** (`pyproject.toml`)
   - Python dependencies
   - Development tool configuration
   - Build settings

## Testing Architecture

### Test Strategy

The testing architecture ensures reliability across all components:

#### 1. Unit Tests (`test_server.py` - 192 lines)
- Individual tool method testing
- Mock Carla controller for isolated testing
- Parameter validation testing
- Error condition coverage

#### 2. Integration Tests (`test_complete_suite.py` - 468 lines)
- End-to-end workflow testing
- Real Carla engine integration
- Multi-tool interaction scenarios
- Performance regression testing

#### 3. Test Organization
- Async test support with pytest-asyncio
- Comprehensive fixture setup
- Mock strategies for external dependencies
- Continuous integration ready

## Error Handling Architecture

### Error Management Strategy

Consistent error handling across all components:

1. **Exception Hierarchy**
   - Base exceptions in `types.py`
   - Component-specific exceptions
   - Error context preservation

2. **Error Response Format**
   ```json
   {
     "success": false,
     "error": "ErrorType",
     "message": "Human-readable description",
     "details": {"additional": "context"}
   }
   ```

3. **Logging Strategy**
   - Structured logging with context
   - Performance metrics logging
   - Error correlation tracking

## Dependency Architecture

### Core Dependencies

#### Production Dependencies
- **mcp**: Model Context Protocol implementation
- **PyQt5**: Carla GUI integration
- **numpy**: Numerical computations for audio analysis
- **scipy**: Signal processing algorithms
- **psutil**: System performance monitoring
- **asyncio-mqtt**: MQTT event streaming
- **aiofiles**: Async file operations
- **pyyaml**: Configuration file parsing

#### Development Dependencies
- **pytest**: Testing framework with async support
- **black**: Code formatting
- **isort**: Import sorting
- **flake8**: Linting
- **mypy**: Type checking
- **pre-commit**: Git hooks

### External System Dependencies

1. **Carla Audio Plugin Host**
   - Python bindings required
   - Plugin format support (VST2/3, LV2, etc.)
   - JACK audio system integration

2. **JACK Audio Connection Kit**
   - Low-latency audio routing
   - Real-time audio processing
   - Cross-application audio connections

3. **Audio Plugins**
   - VST2/VST3 support
   - LV2 plugin format
   - LADSPA/DSSI compatibility
   - AU support (macOS)

## Performance Architecture

### Performance Characteristics

1. **Tool Execution Performance**
   - Async operation design
   - Non-blocking audio operations
   - Efficient parameter caching

2. **Real-Time Constraints**
   - Audio thread safety
   - Low-latency monitoring
   - Minimal audio dropouts

3. **Memory Management**
   - Plugin state caching
   - Session snapshot efficiency
   - Resource cleanup patterns

4. **Scalability Considerations**
   - Multiple session support
   - Plugin instance limits
   - Connection matrix efficiency

## Security Architecture

### Security Considerations

1. **File System Access**
   - Path validation and sanitization
   - Plugin loading security
   - Project file integrity

2. **Network Security**
   - MCP protocol security
   - Client authentication support
   - Rate limiting mechanisms

3. **Process Isolation**
   - Plugin sandboxing via Carla
   - Resource limit enforcement
   - Crash recovery mechanisms

## Development Workflow

### Development Process

1. **Code Quality Gates**
   - Type checking with mypy
   - Code formatting with black
   - Import sorting with isort
   - Linting with flake8

2. **Testing Requirements**
   - Unit test coverage
   - Integration test validation
   - Performance regression testing

3. **Documentation Standards**
   - Comprehensive API documentation
   - Architecture documentation
   - Usage examples and tutorials

## Future Architecture Considerations

### Extensibility Design

1. **Plugin Architecture**
   - Tool plugin system
   - Custom analysis modules
   - Third-party integrations

2. **Protocol Extensions**
   - Additional MCP capabilities
   - Streaming protocol support
   - Real-time collaboration features

3. **Performance Optimization**
   - GPU acceleration for analysis
   - Distributed processing support
   - Advanced caching strategies

---

This architecture supports professional audio production workflows while maintaining code quality, performance, and extensibility for future enhancements.