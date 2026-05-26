#!/usr/bin/env python3
"""
Carla MCP Server - HTTP/SSE Transport
Real MCP-compliant HTTP server using Server-Sent Events
"""

import asyncio
import json
import logging
import sys
import os
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add Carla to path
carla_path = os.environ.get('CARLA_PATH', '/home/gwohl/builds/Carla')
sys.path.append(os.path.join(carla_path, 'source', 'frontend'))

from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
from mcp.server.sse import SseServerTransport
import mcp.types as types

from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import Response, JSONResponse
from starlette.requests import Request
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
log_file = os.path.join(os.path.dirname(__file__), 'carla_mcp_sse_server.log')
logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG to see all MCP messages
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Set MCP library logging to DEBUG
logging.getLogger('mcp').setLevel(logging.DEBUG)


class CarlaMCPSSEServer:
    """MCP Server for Carla with HTTP/SSE transport"""

    def __init__(self, carla_path: str = None):
        """Initialize the Carla MCP SSE Server"""
        self.server = Server("carla-mcp")

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

        # Register all tools and resources
        self._register_tools()
        self._register_resources()

        # Setup event handlers
        self._setup_event_handlers()

        logger.info(f"Carla MCP SSE Server initialized with path: {carla_path}")

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

        @self.server.list_tools()
        async def handle_list_tools() -> list[types.Tool]:
            """Return all available tools from the registry"""
            logger.info("📋 list_tools request received")
            tools = self.tool_registry.get_mcp_tools()
            logger.info(f"📋 Returning {len(tools)} tools")
            return tools

        @self.server.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict | None
        ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
            """Handle tool calls"""
            logger.info(f"🔧 Tool call received: {name} with args: {arguments}")

            start_time = datetime.now()
            self.metrics['tool_calls'] += 1

            try:
                result = await self._execute_tool(name, arguments or {})
                logger.info(f"✅ Tool {name} completed successfully")

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

    def _register_resources(self):
        """Register MCP resources including MixAssist dataset"""

        @self.server.list_resources()
        async def handle_list_resources() -> list[types.Resource]:
            """Return all available resources"""
            resources = []
            resources.extend(mixassist_provider.get_available_resources())
            return resources

        @self.server.read_resource()
        async def handle_read_resource(uri: str) -> str:
            """Read content from a resource"""
            try:
                uri_str = str(uri)

                if uri_str.startswith("mixassist://"):
                    return mixassist_provider.get_resource_content(uri_str)
                else:
                    raise ValueError(f"Unknown resource URI: {uri_str}")

            except Exception as e:
                logger.error(f"Failed to read resource {uri}: {str(e)}")
                raise

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

    def _setup_event_handlers(self):
        """Setup Carla event callbacks"""

        def on_engine_callback(host, action, plugin_id, value1, value2, value3, valuef, value_str):
            """Handle Carla engine callbacks"""
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

    def create_starlette_app(self) -> Starlette:
        """Create Starlette app with SSE transport"""

        # Create SSE transport
        sse = SseServerTransport("/messages/")

        # SSE handler
        async def handle_sse(request: Request):
            """Handle SSE connection from MCP client"""
            logger.info("New SSE connection")
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await self.server.run(
                    streams[0],
                    streams[1],
                    InitializationOptions(
                        server_name="Carla MCP Server",
                        server_version="1.0.0",
                        capabilities=self.server.get_capabilities(
                            notification_options=NotificationOptions(),
                            experimental_capabilities={},
                        ),
                    ),
                )
            return Response()

        # Health check endpoint
        async def health_check(request: Request):
            """Health check endpoint"""
            return JSONResponse({
                "status": "healthy",
                "server": "Carla MCP SSE Server",
                "metrics": self.get_performance_metrics()
            })

        # Root endpoint
        async def root(request: Request):
            """Root endpoint with info"""
            return JSONResponse({
                "name": "Carla MCP Server",
                "version": "1.0.0",
                "transport": "HTTP/SSE",
                "endpoints": {
                    "GET /": "This info",
                    "GET /health": "Health check",
                    "GET /sse": "SSE endpoint for MCP clients",
                    "POST /messages/": "Message endpoint (used by MCP client)"
                },
                "note": "This is an MCP server. Connect using an MCP client with SSE transport."
            })

        # Create routes
        routes = [
            Route("/", endpoint=root, methods=["GET"]),
            Route("/health", endpoint=health_check, methods=["GET"]),
            Route("/sse", endpoint=handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse.handle_post_message),
        ]

        return Starlette(routes=routes)

    def run(self, host: str = "127.0.0.1", port: int = 8765):
        """Run the HTTP/SSE server"""
        try:
            app = self.create_starlette_app()
            logger.info(f"Starting Carla MCP SSE Server on {host}:{port}")
            uvicorn.run(app, host=host, port=port, log_level="info")
        finally:
            # Clean shutdown
            self.ambient_stream.close()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Carla MCP HTTP/SSE Server')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8765, help='Port to bind to')
    parser.add_argument('--carla-path', help='Path to Carla installation')

    args = parser.parse_args()

    # Check for CARLA_PATH in environment
    carla_path = args.carla_path or os.environ.get('CARLA_PATH')

    # Create server
    server = CarlaMCPSSEServer(carla_path=carla_path)

    print(f"\n🎵 Carla MCP HTTP/SSE Server")
    print(f"📡 Listening on http://{args.host}:{args.port}")
    print(f"🔌 SSE endpoint: http://{args.host}:{args.port}/sse")
    print(f"📬 Message endpoint: http://{args.host}:{args.port}/messages/")
    print(f"💚 Health check: http://{args.host}:{args.port}/health")
    print(f"\nConnect using an MCP client with SSE transport configuration:")
    print(f'  "url": "http://{args.host}:{args.port}/sse"')
    print()

    # Run server
    server.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
