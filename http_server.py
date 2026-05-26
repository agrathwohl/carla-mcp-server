#!/usr/bin/env python3
"""
HTTP REST API wrapper for Carla MCP Server
Allows direct testing of all MCP tools via HTTP without Claude Code
"""

import asyncio
import json
import logging
import sys
import os
from typing import Dict, Any, Optional
from datetime import datetime

# Add Carla to path
carla_path = os.environ.get('CARLA_PATH', '/home/gwohl/builds/Carla')
sys.path.append(os.path.join(carla_path, 'source', 'frontend'))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

from carla_controller import CarlaController
from tool_registry import create_carla_tool_registry
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
from monitors.ambient_stream import AmbientStreamLogger
from mixassist_resources import mixassist_provider

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ToolRequest(BaseModel):
    """Request model for tool execution"""
    arguments: Dict[str, Any] = {}


class CarlaHTTPServer:
    """HTTP REST API wrapper for Carla MCP Server"""

    def __init__(self, carla_path: str = None, host: str = "127.0.0.1", port: int = 8765):
        self.app = FastAPI(title="Carla MCP HTTP API", version="1.0.0")
        self.host = host
        self.port = port

        # Auto-detect Carla path if not provided
        if carla_path is None:
            carla_path = self._find_carla_installation()

        # Initialize Carla controller
        self.carla = CarlaController(carla_path)

        # Session management
        self.sessions: Dict[str, Any] = {}
        self.active_session_id: Optional[str] = None

        # Initialize tool registry
        self.tool_registry = create_carla_tool_registry()

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
        self.ambient_stream = AmbientStreamLogger(self.carla, self.event_monitor)

        # Performance metrics
        self.metrics = {
            'tool_calls': 0,
            'errors': 0,
            'avg_response_time': 0,
            'active_plugins': 0,
            'cpu_usage': 0
        }

        # Setup routes
        self._setup_routes()

        logger.info(f"Carla HTTP Server initialized with path: {carla_path}")
        logger.info(f"Will listen on http://{host}:{port}")

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

    def _setup_routes(self):
        """Setup FastAPI routes"""

        @self.app.get("/")
        async def root():
            """API root with documentation"""
            return {
                "name": "Carla MCP HTTP API",
                "version": "1.0.0",
                "endpoints": {
                    "GET /": "This help message",
                    "GET /tools": "List all available tools",
                    "POST /tools/{tool_name}": "Execute a tool",
                    "GET /metrics": "Get performance metrics",
                    "GET /resources": "List available resources",
                    "GET /resources/{resource_uri}": "Get resource content"
                },
                "examples": {
                    "list_plugins": "POST /tools/list_plugins {}",
                    "load_plugin": 'POST /tools/load_plugin {"path": "/usr/lib/lv2/...", "type": "LV2"}',
                    "set_parameter": 'POST /tools/set_parameter {"plugin_id": "0", "parameter_id": 0, "value": 0.5}'
                }
            }

        @self.app.get("/tools")
        async def list_tools():
            """List all available tools"""
            tools = self.tool_registry.get_mcp_tools()
            return {
                "count": len(tools),
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "schema": tool.inputSchema
                    }
                    for tool in tools
                ]
            }

        @self.app.post("/tools/{tool_name}")
        async def execute_tool(tool_name: str, request: ToolRequest):
            """Execute a specific tool"""
            start_time = datetime.now()
            self.metrics['tool_calls'] += 1

            try:
                result = await self._execute_tool(tool_name, request.arguments)

                # Update metrics
                elapsed = (datetime.now() - start_time).total_seconds()
                self.metrics['avg_response_time'] = (
                    (self.metrics['avg_response_time'] * (self.metrics['tool_calls'] - 1) + elapsed)
                    / self.metrics['tool_calls']
                )

                return JSONResponse(content={
                    "success": True,
                    "tool": tool_name,
                    "result": result,
                    "elapsed_ms": int(elapsed * 1000)
                })

            except Exception as e:
                self.metrics['errors'] += 1
                logger.error(f"Tool {tool_name} failed: {str(e)}")
                raise HTTPException(status_code=500, detail={
                    "error": str(e),
                    "tool": tool_name,
                    "arguments": request.arguments
                })

        @self.app.get("/metrics")
        async def get_metrics():
            """Get current performance metrics"""
            return {
                **self.metrics,
                'cpu_usage': self.cpu_monitor.get_current_usage(),
                'active_plugins': self.carla.host.get_current_plugin_count()
            }

        @self.app.get("/resources")
        async def list_resources():
            """List all available resources"""
            resources = mixassist_provider.get_available_resources()
            return {
                "count": len(resources),
                "resources": [
                    {
                        "uri": res.uri,
                        "name": res.name,
                        "description": res.description,
                        "mimeType": res.mimeType
                    }
                    for res in resources
                ]
            }

        @self.app.get("/resources/{resource_path:path}")
        async def get_resource(resource_path: str):
            """Get resource content"""
            try:
                uri = f"mixassist://{resource_path}"
                content = mixassist_provider.get_resource_content(uri)
                return JSONResponse(content={
                    "uri": uri,
                    "content": content
                })
            except Exception as e:
                logger.error(f"Failed to read resource {resource_path}: {str(e)}")
                raise HTTPException(status_code=404, detail=str(e))

    async def _execute_tool(self, name: str, arguments: dict) -> dict:
        """Execute a specific tool"""

        # Log user command to ambient stream
        self.ambient_stream.log_user_command(name, arguments)

        # Add context to arguments
        arguments['session_context'] = self.get_active_session()
        arguments['performance_metrics'] = self.get_performance_metrics()

        # Route to appropriate tool module using registry
        handler = self.tool_registry.get_handler_for_tool(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")

        # Get handler module
        if handler == 'session_tools':
            return await self.session_tools.execute(name, arguments)
        elif handler == 'plugin_tools':
            return await self.plugin_tools.execute(name, arguments)
        elif handler == 'routing_tools':
            return await self.routing_tools.execute(name, arguments)
        elif handler == 'parameter_tools':
            return await self.parameter_tools.execute(name, arguments)
        elif handler == 'analysis_tools':
            return await self.analysis_tools.execute(name, arguments)
        elif handler == 'hardware_tools':
            return await self.hardware_tools.execute(name, arguments)
        elif handler == 'jack_tools':
            return await self.jack_tools.execute(name, arguments)
        else:
            raise ValueError(f"Unknown tool handler: {handler}")

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

    def run(self):
        """Run the HTTP server"""
        try:
            uvicorn.run(self.app, host=self.host, port=self.port)
        finally:
            # Clean shutdown
            self.ambient_stream.close()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Carla MCP HTTP Server')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8765, help='Port to bind to')
    parser.add_argument('--carla-path', help='Path to Carla installation')

    args = parser.parse_args()

    # Check for CARLA_PATH in environment
    carla_path = args.carla_path or os.environ.get('CARLA_PATH')

    # Create and run server
    server = CarlaHTTPServer(carla_path=carla_path, host=args.host, port=args.port)

    print(f"\n🎵 Carla MCP HTTP Server")
    print(f"📡 Listening on http://{args.host}:{args.port}")
    print(f"📚 API docs at http://{args.host}:{args.port}/")
    print(f"\nExample usage:")
    print(f'  curl http://{args.host}:{args.port}/tools')
    print(f'  curl -X POST http://{args.host}:{args.port}/tools/list_plugins -H "Content-Type: application/json" -d "{{}}"')
    print()

    server.run()


if __name__ == "__main__":
    main()
