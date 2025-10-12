# Carla MCP Server

A comprehensive Model Context Protocol (MCP) server for complete control over the Carla audio plugin host. This server enables natural language control of professional audio production workflows through Large Language Models.

## 🎯 Project Overview

The Carla MCP Server provides **45+ tools** across **7 functional categories** for complete audio production control. Built with Python 3.12+, it offers a production-ready interface between AI assistants and professional audio software.

**Key Features:**

- **🤖 AI-Native**: Designed for natural language control through LLMs
- **🎛️ Complete Control**: 45+ tools covering every aspect of audio production
- **⚡ Real-Time**: Low-latency monitoring and analysis capabilities
- **🔧 Professional**: Production-ready with comprehensive error handling and type safety
- **📈 Scalable**: Modular architecture supporting complex workflows

## 🌟 What You Can Do

Ask an AI assistant to help with professional audio tasks:

- _"Load my mixing project and set up a drum bus with compression"_
- _"Create a filter sweep effect on the lead synth, sync it to tempo"_
- _"Analyze my mix and suggest improvements for better frequency balance"_
- _"Set up parallel compression for vocals with different verse/chorus settings"_
- _"Prepare this session for live performance with low latency"_

## 🎛️ Complete Feature Set

### 🗂️ **Session Management (8 tools)**

- Load/save projects with auto-connection
- Create A/B snapshots for comparison
- Hot-swap between sessions with crossfading
- Export/import multiple formats
- Session cleanup and optimization

### 🔌 **Plugin Control (8 tools)**

- Load VST2/3, LV2, LADSPA, DSSI, AU, SF2/SFZ plugins
- Scan directories for available plugins
- Clone and replace plugins with parameter mapping
- Batch processing with plugin chains
- Real-time plugin state control

### 🔗 **Audio Routing (8 tools)**

- Complex audio/MIDI routing matrix
- Bus creation and management with grouping
- Sidechain configuration for compression
- Send/return effect routing
- Connection gain control and automation

### 🎚️ **Parameter Automation (8 tools)**

- Advanced automation with multiple curve types
- MIDI CC mapping with custom ranges
- Macro controls for multiple parameters
- Real-time parameter recording
- Parameter morphing and randomization

### 📊 **Real-Time Analysis (5 tools)**

- Spectrum analysis with customizable FFT
- Audio level metering (Peak, RMS, LUFS)
- Latency measurement and optimization
- Feedback loop detection and prevention
- Parameter capture over time

### 🔊 **JACK Integration (6 tools)**

- JACK port management and connections
- System audio routing
- Port monitoring and status
- Auto-connection for plugins
- Connection stability verification

### 🖥️ **Hardware Control (3+ tools)**

- Audio interface configuration
- Device discovery and management
- Control surface mapping
- Monitor calibration support

## 🚀 Installation

### Prerequisites

1. **Carla Audio Plugin Host**

   ```bash
   # Ubuntu/Debian
   sudo apt install carla carla-dev

   # Or build from source for latest features
   git clone https://github.com/falkTX/Carla.git
   cd Carla
   make
   sudo make install
   ```

2. **Python Environment**

   ```bash
   # Requires Python 3.12+
   python3 --version  # Should be 3.12 or higher
   ```

3. **Audio System**

   ```bash
   # JACK Audio Connection Kit (recommended)
   sudo apt install jackd2 jack-tools

   # For Windows VST support on Linux
   sudo apt install wine wine32 wine64
   ```

### Setup

1. **Clone and Install**

   ```bash
   git clone https://github.com/your-org/carla-mcp-server.git
   cd carla-mcp-server

   # Install dependencies
   pip install -r requirements.txt

   # Or install with development tools
   pip install -e ".[dev]"
   ```

2. **Configure Environment**

   ```bash
   # Set Carla path (adjust to your installation)
   export CARLA_PATH=/usr/share/carla
   export PYTHONPATH=$CARLA_PATH/source/frontend:$PYTHONPATH
   export LD_LIBRARY_PATH=/usr/lib/carla:$LD_LIBRARY_PATH
   ```

3. **Test Installation**

   ```bash
   # Run test suite
   pytest tests/

   # Test Carla integration
   python -c "import carla_backend; print('Carla integration working!')"
   ```

## 🎮 Usage

### Quick Start

```bash
# Start the MCP server
python server.py

# Or with debugging
python server.py --debug --log-level INFO
```

### MCP Client Integration

Add to your MCP client configuration (e.g., Claude Desktop):

```json
{
  "mcpServers": {
    "carla-mcp": {
      "type": "stdio",
      "command": "python",
      "args": ["/path/to/carla-mcp-server/server.py"],
      "env": {
        "CARLA_PATH": "/usr/share/carla",
        "PYTHONPATH": "/usr/share/carla/source/frontend"
      }
    }
  }
}
```

### Natural Language Examples

Once connected to an LLM, you can use natural language for complex audio tasks:

#### Basic Mixing Session

```
User: "Load my rock song project and add warmth to the vocals"

AI: I'll load your project and add some warm character to the vocal track:
1. Loading your rock song session
2. Identifying the lead vocal track
3. Adding tape saturation and warm EQ
4. Fine-tuning the settings for your vocal style

Session loaded! Added vintage warmth to your lead vocals with subtle
tape saturation and a gentle high-frequency roll-off.
```

#### Creative Sound Design

```
User: "Create an evolving filter sweep on the synth pad that builds tension"

AI: I'll create a dynamic filter automation that builds tension:
1. Finding your synth pad plugin
2. Setting up exponential filter frequency automation
3. Adding resonance automation for more character
4. Syncing the sweep to your project tempo

Created a 16-bar filter sweep that starts subtle and builds to an
intense peak, perfect for creating tension in your arrangement.
```

#### Problem Solving

```
User: "Something sounds muddy in my mix and there's a slight feedback"

AI: Let me analyze and fix those issues:
1. Running spectrum analysis to identify muddy frequencies
2. Scanning for feedback loops in your routing
3. Applying corrective EQ to clear up the muddiness
4. Fixing the feedback path I found

Analysis complete! Found buildup around 200-300Hz (applied corrective EQ)
and fixed a feedback loop between your reverb send and input.
Your mix should sound much cleaner now.
```

#### Analyze A Mix's Song Structure

```
User: "Analyze my VU meter's levels for the duration of my song and using
the historic measurement data, identify where in the timecode each verse,
chorus, and solo section begins."

AI: Let me begin analyzing your VU meter plugin for 04:32 minutes (the exact
length of your mix, plus two seconds to accommodate for a slight delay in playback.)

...

Completed my analysis of your VU meter levels! Here is a breakdown of your song's
musical structure based on the historical measurements data I procssed:

1.
```

## 🛠️ Development

### Project Structure

```
carla-mcp-server/
├── server.py              # Main MCP server
├── carla_controller.py     # Carla backend wrapper
├── tool_registry.py       # Tool registration system
├── types.py               # Type definitions
├── base_tools.py          # Base tool framework
├── tools/                 # MCP tool implementations
│   ├── session_tools.py   # Session management
│   ├── plugin_tools.py    # Plugin control
│   ├── routing_tools.py   # Audio routing
│   ├── parameter_tools.py # Parameter automation
│   ├── analysis_tools.py  # Real-time analysis
│   ├── jack_tools.py      # JACK integration
│   └── hardware_tools.py  # Hardware control
├── monitors/              # Real-time monitoring
│   ├── audio_monitor.py   # Audio level monitoring
│   ├── cpu_monitor.py     # Performance monitoring
│   └── event_monitor.py   # Event streaming
├── tests/                 # Comprehensive test suite
└── config/                # Configuration files
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=carla_mcp_server --cov-report=html

# Run specific test category
pytest tests/test_server.py::TestSessionManagement

# Run integration tests (requires Carla)
pytest tests/test_complete_suite.py
```

### Code Quality

```bash
# Format code
black carla_mcp_server/

# Sort imports
isort carla_mcp_server/

# Type checking
mypy carla_mcp_server/

# Linting
flake8 carla_mcp_server/

# Run all quality checks
pre-commit run --all-files
```

### Adding New Tools

1. **Create Tool Method** in appropriate `tools/` module:

   ```python
   async def my_new_tool(self, param1: str, param2: int = 10) -> dict:
       """Tool description for documentation."""
       try:
           # Implementation
           return {"success": True, "result": data}
       except Exception as e:
           return {"success": False, "error": str(e)}
   ```

2. **Register Tool** in `tool_registry.py`:

   ```python
   ToolDefinition(
       name="my_new_tool",
       description="Description for MCP clients",
       handler="tool_class_name",
       input_schema={/* JSON schema */}
   )
   ```

3. **Add Tests** in `tests/`:
   ```python
   async def test_my_new_tool():
       # Test implementation
   ```

## 🧩 Complete Tool Reference

### Session Management

- `load_session` - Load Carla project files
- `save_session` - Save current session state
- `create_snapshot` - Create A/B comparison snapshots
- `switch_session` - Hot-swap between sessions
- `list_sessions` - Show available sessions
- `delete_session` - Remove sessions
- `export_session` - Export to audio formats
- `import_session` - Import from external formats

### Plugin Control

- `load_plugin` - Load any plugin format
- `scan_plugins` - Discover available plugins
- `control_plugin` - Activate/bypass/solo/remove
- `batch_process` - Apply plugin chains to audio
- `list_plugins` - Show loaded plugins
- `get_plugin_info` - Detailed plugin information
- `clone_plugin` - Duplicate plugins with settings
- `replace_plugin` - Swap plugins with parameter mapping

### Audio Routing

- `connect_audio` - Create audio connections
- `create_bus` - Build audio buses for grouping
- `setup_sidechain` - Configure sidechain routing
- `get_routing_matrix` - View complete routing
- `disconnect_audio` - Remove connections
- `create_send` - Set up send/return effects
- `set_connection_gain` - Adjust connection levels

### Parameter Automation

- `automate_parameter` - Create automation curves
- `map_midi_cc` - MIDI controller mapping
- `create_macro` - Multi-parameter macros
- `record_automation` - Capture parameter changes
- `set_parameter` - Direct parameter control
- `get_parameter` - Read parameter values
- `randomize_parameters` - Creative randomization
- `morph_parameters` - Smooth parameter transitions

### Real-Time Analysis

- `analyze_spectrum` - FFT spectrum analysis
- `measure_levels` - Peak/RMS/LUFS metering
- `capture_plugin_parameters` - Parameter monitoring
- `detect_feedback` - Feedback loop detection
- `analyze_latency` - System latency measurement

### JACK Integration

- `list_jack_ports` - Show available JACK ports
- `connect_jack_ports` - Connect JACK ports
- `disconnect_jack_ports` - Disconnect JACK ports
- `get_jack_connections` - View port connections
- `connect_system_to_plugin` - Route system audio to plugins
- `connect_plugin_to_system` - Route plugins to system output

### Hardware Control

- `configure_audio_interface` - Set up audio hardware
- `list_audio_devices` - Discover audio devices
- `map_control_surface` - Configure MIDI controllers

## 📖 Documentation

- **[API.md](API.md)** - Complete tool reference with examples
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Technical architecture documentation
- **[CONTRIBUTING.md](CONTRIBUTING.md)** - Development guidelines

## 🔧 Configuration

### Server Configuration (`config/config.yaml`)

```yaml
server:
  host: localhost
  port: 8765
  log_level: INFO

carla:
  path: /usr/share/carla
  wine_prefix: ~/.wine
  auto_start_engine: true

audio:
  driver: JACK
  sample_rate: 48000
  buffer_size: 512
  auto_connect: true

plugins:
  scan_paths:
    - /usr/lib/lv2
    - /usr/lib/vst
    - ~/.wine/drive_c/Program Files/Common Files/VST3
  cache_enabled: true

monitoring:
  audio_enabled: true
  cpu_enabled: true
  update_interval_ms: 100
```

### Environment Variables

```bash
# Essential paths
export CARLA_PATH=/usr/share/carla
export PYTHONPATH=$CARLA_PATH/source/frontend:$PYTHONPATH
export LD_LIBRARY_PATH=/usr/lib/carla:$LD_LIBRARY_PATH

# Optional configuration
export CARLA_MCP_LOG_LEVEL=INFO
export CARLA_MCP_HOST=localhost
export CARLA_MCP_PORT=8765
```

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

**Quick Contribution Steps:**

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## 📄 License

This project is licensed under the GPL-2.0-or-later License - see the [LICENSE](LICENSE) file for details.

## 🙏 Credits

- Built on top of [Carla](https://github.com/falkTX/Carla) by falkTX
- Uses the [Model Context Protocol](https://modelcontextprotocol.io/) by Anthropic
- Inspired by the audio production community

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/your-org/carla-mcp-server/issues)
- **Discussions**: [GitHub Discussions](https://github.com/your-org/carla-mcp-server/discussions)
- **Documentation**: See `docs/` directory
- **Community**: Join the Carla Discord community

---

**Ready to revolutionize your audio production workflow with AI assistance? Get started today!** 🎵✨
