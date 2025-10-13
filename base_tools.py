"""Base classes for Carla MCP tool handlers.

This module provides base classes and utilities that eliminate code duplication
across tool modules and provide consistent error handling patterns.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Dict, Callable, Any, Optional
from datetime import datetime

# Remove broken import - these types are not defined

logger = logging.getLogger(__name__)


class BaseToolHandler(ABC):
    """Base class for all Carla MCP tool handlers.

    This class provides:
    - Consistent tool execution routing
    - Standardized error handling
    - Common utility methods
    - Performance metrics
    """

    def __init__(self, carla_controller: CarlaController):
        """Initialize the tool handler.

        Args:
            carla_controller: CarlaController instance
        """
        self.carla = carla_controller
        self._tool_registry: Dict[str, Callable] = {}
        self._metrics = {
            'executions': 0,
            'errors': 0,
            'total_time': 0.0,
        }

        # Register tools during initialization
        self._register_tools()

        logger.info(f"{self.__class__.__name__} initialized with {len(self._tool_registry)} tools")

    @abstractmethod
    def _register_tools(self) -> None:
        """Register all tools for this handler.

        Subclasses should implement this method to populate self._tool_registry
        with tool_name -> method mappings.
        """
        pass

    def register_tool(self, name: str, method: Callable) -> None:
        """Register a single tool method.

        Args:
            name: Tool name
            method: Method to call for this tool

        Raises:
            ToolRegistrationError: If tool name already exists
        """
        if name in self._tool_registry:
            raise ToolRegistrationError(f"Tool '{name}' already registered in {self.__class__.__name__}")

        self._tool_registry[name] = method
        logger.debug(f"Registered tool '{name}' in {self.__class__.__name__}")

    async def execute(self, tool_name: str, arguments: JsonDict) -> ToolResult:
        """Execute a tool with standardized error handling.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments

        Returns:
            Tool execution result with success/error structure

        Raises:
            ValueError: If tool is not found
        """
        start_time = datetime.now()
        self._metrics['executions'] += 1

        try:
            # Validate tool exists
            if tool_name not in self._tool_registry:
                available_tools = list(self._tool_registry.keys())
                raise ValueError(
                    f"Unknown tool '{tool_name}' in {self.__class__.__name__}. "
                    f"Available tools: {available_tools}"
                )

            # Clean arguments - remove context fields if present
            clean_args = self._clean_arguments(arguments)

            # Execute the tool
            tool_method = self._tool_registry[tool_name]
            result = await tool_method(**clean_args)

            # Ensure result has proper structure
            if not isinstance(result, dict):
                result = {'success': True, 'result': result}
            elif 'success' not in result:
                result['success'] = True

            # Add execution metadata
            result['tool_name'] = tool_name
            result['handler'] = self.__class__.__name__
            result['execution_time_ms'] = (datetime.now() - start_time).total_seconds() * 1000

            self._update_metrics(start_time, success=True)
            return result

        except Exception as e:
            self._metrics['errors'] += 1
            self._update_metrics(start_time, success=False)

            error_result = {
                'success': False,
                'error': str(e),
                'error_type': type(e).__name__,
                'tool_name': tool_name,
                'handler': self.__class__.__name__,
                'execution_time_ms': (datetime.now() - start_time).total_seconds() * 1000,
            }

            logger.error(f"Tool '{tool_name}' failed in {self.__class__.__name__}: {str(e)}")
            return error_result

    def _clean_arguments(self, arguments: JsonDict) -> JsonDict:
        """Remove context fields that shouldn't be passed to tool methods.

        Args:
            arguments: Raw arguments from tool call

        Returns:
            Cleaned arguments dictionary
        """
        # Remove common context fields added by the server
        context_fields = {
            'session_context',
            'performance_metrics',
            'timestamp',
            'request_id',
        }

        return {k: v for k, v in arguments.items() if k not in context_fields}

    def _update_metrics(self, start_time: datetime, success: bool) -> None:
        """Update performance metrics.

        Args:
            start_time: When execution started
            success: Whether execution was successful
        """
        elapsed = (datetime.now() - start_time).total_seconds()
        self._metrics['total_time'] += elapsed

    def get_metrics(self) -> JsonDict:
        """Get performance metrics for this handler.

        Returns:
            Dictionary containing execution statistics
        """
        metrics = self._metrics.copy()
        if metrics['executions'] > 0:
            metrics['avg_execution_time'] = metrics['total_time'] / metrics['executions']
            metrics['error_rate'] = metrics['errors'] / metrics['executions']
        else:
            metrics['avg_execution_time'] = 0.0
            metrics['error_rate'] = 0.0

        metrics['available_tools'] = list(self._tool_registry.keys())
        return metrics

    def get_tool_names(self) -> list[str]:
        """Get list of available tool names.

        Returns:
            List of tool names supported by this handler
        """
        return list(self._tool_registry.keys())

    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool is available.

        Args:
            tool_name: Tool name to check

        Returns:
            True if tool is available
        """
        return tool_name in self._tool_registry


# Utility functions for common operations
def safe_execute(func: Callable, *args, **kwargs) -> ToolResult:
    """Safely execute a function with error handling.

    Args:
        func: Function to execute
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        Result dictionary with success/error structure
    """
    try:
        result = func(*args, **kwargs)
        return {
            'success': True,
            'result': result
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'error_type': type(e).__name__
        }


def validate_plugin_id(plugin_id: Any, carla_controller: CarlaController) -> int:
    """Validate and convert plugin ID to integer.

    Args:
        plugin_id: Plugin ID to validate
        carla_controller: Carla controller to check against

    Returns:
        Valid integer plugin ID

    Raises:
        ValueError: If plugin ID is invalid
    """
    try:
        plugin_id = int(plugin_id)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid plugin ID: {plugin_id}")

    plugin_count = carla_controller.host.get_current_plugin_count()
    if plugin_id < 0 or plugin_id >= plugin_count:
        raise ValueError(f"Plugin ID {plugin_id} out of range (0-{plugin_count-1})")

    return plugin_id


def validate_parameter_id(parameter_id: Any, plugin_id: int, carla_controller: CarlaController) -> int:
    """Validate parameter ID for a plugin.

    Args:
        parameter_id: Parameter ID to validate
        plugin_id: Plugin ID that owns the parameter
        carla_controller: Carla controller to check against

    Returns:
        Valid integer parameter ID

    Raises:
        ValueError: If parameter ID is invalid
    """
    try:
        parameter_id = int(parameter_id)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid parameter ID: {parameter_id}")

    param_count = carla_controller.host.get_parameter_count(plugin_id)
    if parameter_id < 0 or parameter_id >= param_count:
        raise ValueError(f"Parameter ID {parameter_id} out of range for plugin {plugin_id} (0-{param_count-1})")

    return parameter_id


class ToolRegistry:
    """Registry for managing tool handlers."""

    def __init__(self):
        """Initialize empty tool registry."""
        self._handlers: Dict[str, BaseToolHandler] = {}
        self._tool_to_handler: Dict[str, str] = {}

    def register_handler(self, name: str, handler: BaseToolHandler) -> None:
        """Register a tool handler.

        Args:
            name: Handler name
            handler: Handler instance

        Raises:
            ToolRegistrationError: If handler name conflicts
        """
        if name in self._handlers:
            raise ToolRegistrationError(f"Handler '{name}' already registered")

        self._handlers[name] = handler

        # Map tools to handlers
        for tool_name in handler.get_tool_names():
            if tool_name in self._tool_to_handler:
                existing_handler = self._tool_to_handler[tool_name]
                raise ToolRegistrationError(
                    f"Tool '{tool_name}' conflicts between handlers '{existing_handler}' and '{name}'"
                )
            self._tool_to_handler[tool_name] = name

        logger.info(f"Registered handler '{name}' with {len(handler.get_tool_names())} tools")

    def get_handler_for_tool(self, tool_name: str) -> Optional[BaseToolHandler]:
        """Get the handler for a specific tool.

        Args:
            tool_name: Tool name to find handler for

        Returns:
            Handler instance or None if not found
        """
        handler_name = self._tool_to_handler.get(tool_name)
        if handler_name:
            return self._handlers[handler_name]
        return None

    def get_all_tools(self) -> list[str]:
        """Get list of all available tools.

        Returns:
            List of all tool names across all handlers
        """
        return list(self._tool_to_handler.keys())

    def get_handlers(self) -> Dict[str, BaseToolHandler]:
        """Get all registered handlers.

        Returns:
            Dictionary mapping handler names to instances
        """
        return self._handlers.copy()


# Export public interface
__all__ = [
    "BaseToolHandler",
    "ToolRegistry",
    "safe_execute",
    "validate_plugin_id",
    "validate_parameter_id",
]