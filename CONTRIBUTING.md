# Contributing to Carla MCP Server

Thank you for considering contributing to the Carla MCP Server! This document provides guidelines and information for contributors.

## 🎯 Project Overview

The Carla MCP Server enables natural language control of professional audio production through Large Language Models. We welcome contributions that enhance functionality, improve performance, fix bugs, or expand documentation.

## 🚀 Quick Start for Contributors

### 1. Development Environment Setup

```bash
# Fork and clone the repository
git clone https://github.com/your-username/carla-mcp-server.git
cd carla-mcp-server

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install development dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

### 2. Install Dependencies

**System Dependencies:**
```bash
# Ubuntu/Debian
sudo apt install carla carla-dev jackd2 jack-tools

# Development tools
sudo apt install build-essential python3-dev
```

**Environment Setup:**
```bash
# Set required environment variables
export CARLA_PATH=/usr/share/carla
export PYTHONPATH=$CARLA_PATH/source/frontend:$PYTHONPATH
export LD_LIBRARY_PATH=/usr/lib/carla:$LD_LIBRARY_PATH
```

### 3. Verify Installation

```bash
# Run the test suite
pytest

# Test Carla integration
python -c "import carla_backend; print('✅ Carla integration working!')"

# Start the server in development mode
python server.py --debug
```

## 📋 Contribution Types

We welcome several types of contributions:

### 🔧 **Code Contributions**
- **New Tools**: Add new MCP tools for audio production workflows
- **Bug Fixes**: Fix issues in existing functionality
- **Performance Improvements**: Optimize audio processing or tool execution
- **Test Coverage**: Add or improve test coverage

### 📚 **Documentation**
- **API Documentation**: Improve tool documentation and examples
- **Tutorials**: Create workflow guides and examples
- **Architecture**: Document design decisions and patterns

### 🎨 **User Experience**
- **Natural Language Processing**: Improve LLM interaction patterns
- **Error Messages**: Make error messages more helpful
- **Configuration**: Simplify setup and configuration

### 🐛 **Bug Reports**
- **Issue Reports**: Well-documented bug reports with reproduction steps
- **Performance Issues**: Audio dropouts, latency problems, memory leaks

## 🛠️ Development Workflow

### 1. Choose an Issue or Feature

- Check [GitHub Issues](https://github.com/your-org/carla-mcp-server/issues)
- Look for `good-first-issue` or `help-wanted` labels
- For new features, discuss in an issue first

### 2. Create a Feature Branch

```bash
# Create and switch to a new branch
git checkout -b feature/your-feature-name

# Or for bug fixes
git checkout -b fix/issue-description
```

### 3. Development Process

**Code Style:**
```bash
# Format code
black .

# Sort imports
isort .

# Type checking
mypy carla_mcp_server/

# Linting
flake8 carla_mcp_server/
```

**Testing:**
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_session_tools.py

# Run with coverage
pytest --cov=carla_mcp_server --cov-report=html
```

### 4. Commit Guidelines

Use conventional commits:
```bash
# Features
git commit -m "feat(session): add session hot-swap functionality"

# Bug fixes
git commit -m "fix(plugin): resolve VST3 loading on Windows"

# Documentation
git commit -m "docs(api): add natural language examples"

# Tests
git commit -m "test(routing): add sidechain configuration tests"
```

### 5. Pull Request Process

1. **Push your branch**:
   ```bash
   git push origin feature/your-feature-name
   ```

2. **Create Pull Request** on GitHub with:
   - Clear title and description
   - Reference any related issues
   - List what was changed and why
   - Include testing instructions

3. **Code Review Process**:
   - Maintainers will review your code
   - Address feedback promptly
   - Keep discussions constructive

## 🧪 Testing Guidelines

### Test Structure

```python
# tests/test_new_feature.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from carla_mcp_server.tools.new_tools import NewTools


class TestNewTools:
    @pytest.fixture
    def mock_carla_controller(self):
        """Mock Carla controller for isolated testing."""
        mock = MagicMock()
        mock.get_current_plugin_count.return_value = 0
        return mock

    @pytest.fixture
    def new_tools(self, mock_carla_controller):
        """Create NewTools instance with mocked dependencies."""
        return NewTools(mock_carla_controller)

    async def test_new_tool_success(self, new_tools):
        """Test successful tool execution."""
        result = await new_tools.my_new_tool("test_param")

        assert result["success"] is True
        assert "result" in result

    async def test_new_tool_error_handling(self, new_tools, mock_carla_controller):
        """Test error handling in tool execution."""
        mock_carla_controller.some_method.side_effect = Exception("Test error")

        result = await new_tools.my_new_tool("test_param")

        assert result["success"] is False
        assert "error" in result
```

### Integration Tests

```python
# tests/test_integration.py
@pytest.mark.integration
async def test_full_workflow():
    """Test complete workflow with real Carla instance."""
    # Only run if Carla is available
    if not carla_available():
        pytest.skip("Carla not available")

    # Test implementation
```

### Running Tests

```bash
# Unit tests only
pytest tests/ -m "not integration"

# Integration tests (requires Carla)
pytest tests/ -m "integration"

# All tests
pytest

# Specific test with verbose output
pytest tests/test_session_tools.py::TestSessionTools::test_load_session -v
```

## 🔧 Adding New Tools

### 1. Create Tool Implementation

```python
# tools/my_new_tools.py
from typing import Dict, Any, Optional
from ..base_tools import BaseTools


class MyNewTools(BaseTools):
    """Tools for new functionality."""

    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Route tool execution to appropriate method."""
        if tool_name == "my_new_tool":
            return await self.my_new_tool(**arguments)
        else:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

    async def my_new_tool(self, param1: str, param2: int = 10) -> Dict[str, Any]:
        """
        Tool description that will appear in documentation.

        Args:
            param1: Description of parameter 1
            param2: Description of parameter 2 with default

        Returns:
            Dict with success status and result data
        """
        try:
            # Validate parameters
            if not param1:
                raise ValueError("param1 cannot be empty")

            # Execute tool logic
            result = self.carla.some_carla_operation(param1, param2)

            return {
                "success": True,
                "result": result,
                "param1": param1,
                "param2": param2
            }

        except Exception as e:
            logger.error(f"my_new_tool failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }
```

### 2. Register the Tool

```python
# tool_registry.py - Add to appropriate section
ToolDefinition(
    name="my_new_tool",
    description="Brief description for MCP clients",
    handler="my_new_tools",
    input_schema={
        "type": "object",
        "properties": {
            "param1": {
                "type": "string",
                "description": "Description of parameter 1"
            },
            "param2": {
                "type": "integer",
                "description": "Description of parameter 2",
                "default": 10
            }
        },
        "required": ["param1"]
    },
    examples=[
        "my_new_tool('hello')",
        "my_new_tool('world', param2=20)"
    ]
)
```

### 3. Add to Server Registration

```python
# server.py - Add to __init__ method
self.my_new_tools = MyNewTools(self.carla)

# Add to _register_tools method
tools_mapping = {
    # ... existing tools ...
    "my_new_tools": self.my_new_tools,
}
```

### 4. Write Tests

```python
# tests/test_my_new_tools.py
class TestMyNewTools:
    async def test_my_new_tool_success(self, my_new_tools):
        result = await my_new_tools.my_new_tool("test")
        assert result["success"] is True

    async def test_my_new_tool_validation(self, my_new_tools):
        result = await my_new_tools.my_new_tool("")
        assert result["success"] is False
        assert "empty" in result["error"]
```

### 5. Update Documentation

Add your tool to the appropriate sections in:
- `API.md` - Complete tool reference
- `README.md` - Tool listing
- Add natural language examples showing how an LLM would use your tool

## 📐 Code Style Guidelines

### Python Code Style

We follow PEP 8 with some project-specific conventions:

**Formatting:**
- Line length: 88 characters (Black default)
- Use double quotes for strings
- 4 spaces for indentation

**Naming Conventions:**
```python
# Classes: PascalCase
class SessionTools:

# Functions/methods: snake_case
async def load_session(self):

# Constants: UPPER_SNAKE_CASE
MAX_PLUGINS = 64

# Private methods: leading underscore
def _validate_plugin_id(self):
```

**Type Hints:**
```python
from typing import Dict, List, Optional, Union

async def my_tool(self,
                  param1: str,
                  param2: Optional[int] = None
                  ) -> Dict[str, Any]:
    """Always use type hints for function parameters and returns."""
```

**Docstrings:**
```python
async def load_plugin(self, path: str, plugin_type: str) -> Dict[str, Any]:
    """
    Load a plugin into Carla.

    Args:
        path: Absolute path to plugin file or URI
        plugin_type: Plugin type (VST2, VST3, LV2, etc.)

    Returns:
        Dict containing success status and plugin information

    Raises:
        PluginError: If plugin loading fails
        ValueError: If parameters are invalid
    """
```

### Error Handling

**Consistent Error Format:**
```python
# Success response
return {
    "success": True,
    "result": data,
    "additional_info": "if needed"
}

# Error response
return {
    "success": False,
    "error": "Human-readable error message",
    "error_type": "ErrorClassName",
    "details": {"context": "additional details"}
}
```

**Exception Handling:**
```python
try:
    # Operation that might fail
    result = self.carla.load_plugin(path)

except FileNotFoundError:
    return {
        "success": False,
        "error": f"Plugin file not found: {path}",
        "error_type": "FileNotFoundError"
    }
except Exception as e:
    logger.error(f"Unexpected error in load_plugin: {e}")
    return {
        "success": False,
        "error": "Internal error occurred",
        "error_type": type(e).__name__
    }
```

## 🎵 Audio Development Guidelines

### Real-Time Considerations

**Audio Thread Safety:**
```python
# Avoid blocking operations in audio-critical paths
async def audio_sensitive_tool(self):
    # Good: Quick parameter read
    value = self.carla.get_parameter_value(plugin_id, param_id)

    # Bad: File I/O in audio context
    # await self.save_session("/path/to/file.carxp")
```

**Latency Optimization:**
```python
# Cache frequently accessed data
class PluginTools:
    def __init__(self, carla_controller):
        self.carla = carla_controller
        self._plugin_cache = {}

    async def get_plugin_info(self, plugin_id: str):
        if plugin_id in self._plugin_cache:
            return self._plugin_cache[plugin_id]
        # ... fetch and cache
```

### JACK Integration

**Connection Management:**
```python
# Always verify connections before creating
existing_connections = await self.get_jack_connections()
if (source, destination) not in existing_connections:
    await self.connect_jack_ports(source, destination)
```

## 🔍 Performance Guidelines

### Async Best Practices

```python
# Good: Non-blocking operations
async def batch_process_plugins(self, plugin_ids: List[str]):
    tasks = []
    for plugin_id in plugin_ids:
        task = asyncio.create_task(self.process_plugin(plugin_id))
        tasks.append(task)

    results = await asyncio.gather(*tasks, return_exceptions=True)
    return results

# Bad: Sequential blocking
async def batch_process_plugins_bad(self, plugin_ids: List[str]):
    results = []
    for plugin_id in plugin_ids:
        result = await self.process_plugin(plugin_id)  # Blocks others
        results.append(result)
    return results
```

### Memory Management

```python
# Clean up resources
class AnalysisTools:
    def __init__(self, carla_controller):
        self.carla = carla_controller
        self._spectrum_buffer = None

    async def analyze_spectrum(self, source: str):
        try:
            # Allocate buffer if needed
            if self._spectrum_buffer is None:
                self._spectrum_buffer = np.zeros(2048)

            # Use buffer for analysis
            # ...

        finally:
            # Clean up if needed
            pass

    def cleanup(self):
        """Call when shutting down."""
        self._spectrum_buffer = None
```

## 🤝 Community Guidelines

### Communication

- **Be Respectful**: Treat all community members with respect
- **Be Constructive**: Provide helpful feedback and suggestions
- **Be Patient**: Remember that maintainers are volunteers
- **Be Clear**: Provide detailed information in issues and PRs

### Issue Reporting

**Bug Reports:**
```markdown
## Bug Description
Clear description of the issue

## Steps to Reproduce
1. Load session with X plugins
2. Execute tool Y with parameters Z
3. Observe error

## Expected Behavior
What should have happened

## Actual Behavior
What actually happened

## Environment
- OS: Ubuntu 22.04
- Python: 3.12.1
- Carla: 2.5.8
- JACK: 1.9.21

## Logs
```
[Paste relevant log output]
```

**Feature Requests:**
```markdown
## Feature Description
Clear description of the proposed feature

## Use Case
Why this feature would be valuable

## Proposed Implementation
How it might work (optional)

## Natural Language Example
"Create a macro that controls reverb send and dry/wet balance"
```

## 📚 Documentation Standards

### API Documentation

```python
async def create_macro(self,
                      name: str,
                      targets: List[Dict[str, Any]]
                      ) -> Dict[str, Any]:
    """
    Create a macro control that manages multiple parameters.

    A macro allows controlling multiple plugin parameters with a single
    control, useful for creating complex musical effects or performance
    controls.

    Args:
        name: Human-readable name for the macro (e.g., "Filter Sweep")
        targets: List of parameter targets, each containing:
            - plugin_id (str): Target plugin identifier
            - parameter_id (int): Parameter index within plugin
            - range (Dict): Min/max values {"min": 0.0, "max": 1.0}
            - curve (str): Response curve ("linear", "exponential", "logarithmic")
            - inverted (bool): Whether to invert the response

    Returns:
        Dict containing:
            - success (bool): Whether macro creation succeeded
            - macro_id (str): Unique identifier for the created macro
            - targets (int): Number of parameters mapped
            - name (str): The macro name

    Example:
        >>> result = await tools.create_macro(
        ...     name="Vocal Intensity",
        ...     targets=[
        ...         {
        ...             "plugin_id": "eq_1",
        ...             "parameter_id": 0,
        ...             "range": {"min": 200, "max": 2000},
        ...             "curve": "exponential"
        ...         },
        ...         {
        ...             "plugin_id": "comp_1",
        ...             "parameter_id": 3,
        ...             "range": {"min": 1, "max": 10},
        ...             "curve": "linear"
        ...         }
        ...     ]
        ... )
        >>> print(result["macro_id"])
        "macro_vocal_intensity_001"

    Natural Language Usage:
        "Create a macro called 'Build Up' that increases the filter frequency
         and adds more compression as I turn it up"

    Raises:
        ValueError: If macro name is empty or targets list is invalid
        PluginError: If any target plugin is not found
    """
```

### Natural Language Examples

Always include examples showing how an LLM would use the tool:

```markdown
### Natural Language Usage

**User Prompt:** *"Create a filter sweep macro for the lead synth"*

**LLM Response:**
```
I'll create a filter sweep macro for your lead synth that you can control
with a single parameter:

1. Finding your lead synth plugin
2. Setting up filter frequency automation (20Hz to 20kHz)
3. Adding resonance control for more character
4. Creating the macro control

Macro "Lead Filter Sweep" created! You can now control both filter
frequency and resonance with a single control.
```

**Tools Called:**
1. `list_plugins` - Find lead synth
2. `get_plugin_info` - Get filter parameter info
3. `create_macro` - Create the macro control
```

## ⚡ Performance Testing

### Benchmarking Tools

```python
# tests/performance/test_benchmarks.py
import time
import pytest
from carla_mcp_server.tools.plugin_tools import PluginTools


class TestPerformance:
    @pytest.mark.performance
    async def test_plugin_loading_speed(self, plugin_tools):
        """Test plugin loading performance."""
        start_time = time.time()

        # Load multiple plugins
        for i in range(10):
            result = await plugin_tools.load_plugin(
                path=f"/path/to/test_plugin_{i}.vst3",
                plugin_type="VST3"
            )
            assert result["success"]

        end_time = time.time()
        loading_time = end_time - start_time

        # Should load 10 plugins in under 5 seconds
        assert loading_time < 5.0

        print(f"Loaded 10 plugins in {loading_time:.2f}s")
```

### Memory Profiling

```python
# Use memory_profiler for memory testing
@profile
async def test_memory_usage():
    # Tool operations that should not leak memory
    pass
```

## 🚀 Release Process

### Version Management

We use semantic versioning (SemVer):
- **Major** (1.0.0): Breaking changes
- **Minor** (1.1.0): New features, backwards compatible
- **Patch** (1.1.1): Bug fixes, backwards compatible

### Changelog Format

```markdown
# Changelog

## [1.2.0] - 2025-01-15

### Added
- New parameter morphing tool for smooth transitions
- JACK auto-connection for system audio
- Performance monitoring with CPU usage tracking

### Changed
- Improved error messages for plugin loading failures
- Updated API documentation with more natural language examples

### Fixed
- VST3 plugin scanning on Windows systems
- Memory leak in spectrum analysis tool
- JACK port connection stability issues

### Deprecated
- Legacy session format support (will be removed in 2.0.0)
```

## 🎯 Roadmap and Priorities

### Current Focus Areas

1. **Stability**: Robust error handling and recovery
2. **Performance**: Low-latency audio operations
3. **Usability**: Better natural language interaction patterns
4. **Documentation**: Comprehensive guides and examples

### Future Enhancements

- **Real-time Collaboration**: Multi-user session support
- **Cloud Integration**: Remote session storage and sharing
- **Advanced AI**: Intelligent mixing suggestions
- **Plugin Development**: SDK for custom audio tools

## 🏆 Recognition

Contributors are recognized in:
- `README.md` contributors section
- Release notes for significant contributions
- Special recognition for first-time contributors

## 📞 Getting Help

- **GitHub Discussions**: For general questions and ideas
- **Discord**: Real-time chat with the community
- **Documentation**: Check existing docs first
- **Issues**: For bugs and feature requests

---

Thank you for contributing to the Carla MCP Server! Your contributions help make professional audio production more accessible through AI assistance. 🎵✨