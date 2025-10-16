"""
Ambient Dashboard Stream Logger for Carla MCP Server

Provides minimalist real-time status updates to /tmp/carla-stream
for tail -f monitoring during audio sessions.
"""

import os
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

class AmbientStreamLogger:
    """Minimalist ambient dashboard stream logger"""

    def __init__(self, carla_controller, event_monitor, stream_path: str = "/tmp/carla-stream"):
        """Initialize ambient stream logger

        Args:
            carla_controller: CarlaController instance
            event_monitor: EventMonitor instance to subscribe to
            stream_path: Path to output stream file
        """
        self.carla = carla_controller
        self.event_monitor = event_monitor
        self.stream_path = stream_path
        self.transport_state = {
            'is_playing': False,
            'is_recording': False,
            'start_time': None,
            'pause_time': None
        }

        # Initialize stream file
        self._init_stream_file()

        # Subscribe to relevant Carla events
        self._subscribe_to_events()

        logger.info(f"AmbientStreamLogger initialized, streaming to {stream_path}")

    def _init_stream_file(self):
        """Initialize the stream file"""
        try:
            # Create directory if needed
            os.makedirs(os.path.dirname(self.stream_path), exist_ok=True)

            # Initialize with startup message
            startup_time = self._format_military_time()
            with open(self.stream_path, 'w') as f:
                f.write(f"{startup_time} 🚀 carla session started\n")
        except Exception as e:
            logger.error(f"Failed to initialize stream file: {e}")

    def _subscribe_to_events(self):
        """Subscribe to relevant EventMonitor events"""

        # Plugin events
        self.event_monitor.subscribe('CALLBACK_PLUGIN_ADDED', self._handle_plugin_event)
        self.event_monitor.subscribe('CALLBACK_PLUGIN_REMOVED', self._handle_plugin_event)

        # Connection events
        self.event_monitor.subscribe('CALLBACK_PATCHBAY_CONNECTION_ADDED', self._handle_connection_event)
        self.event_monitor.subscribe('CALLBACK_PATCHBAY_CONNECTION_REMOVED', self._handle_connection_event)

        # Transport events
        self.event_monitor.subscribe('CALLBACK_TRANSPORT_MODE_CHANGED', self._handle_transport_event)

        # Parameter events
        self.event_monitor.subscribe('CALLBACK_PARAMETER_VALUE_CHANGED', self._handle_parameter_event)

        # Audio events (clipping, etc.)
        self.event_monitor.subscribe('CALLBACK_ENGINE_CALLBACK', self._handle_engine_event)

    def _format_military_time(self) -> str:
        """Format current time as military time HH:MM:SS"""
        return datetime.now().strftime("%H:%M:%S")

    def _format_protools_timecode(self, seconds: float) -> str:
        """Format time as Pro Tools style timecode MM:SS.mmm"""
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes:02d}:{secs:06.3f}"

    def _append_to_stream(self, message: str):
        """Append message to stream file"""
        try:
            with open(self.stream_path, 'a') as f:
                f.write(f"{message}\n")
        except Exception as e:
            logger.error(f"Failed to write to stream: {e}")

    def log_user_command(self, tool_name: str, arguments: Dict[str, Any]):
        """Log user command to stream

        Args:
            tool_name: Name of the MCP tool being called
            arguments: Tool arguments
        """
        timestamp = self._format_military_time()

        # Format command based on tool type
        if tool_name.startswith('load_plugin'):
            plugin_name = arguments.get('path', 'unknown').split('/')[-1]
            message = f"{timestamp} 🎛️  load_plugin {plugin_name}"

        elif tool_name.startswith('set_parameter'):
            plugin_id = arguments.get('plugin_id', 'unknown')
            param_id = arguments.get('parameter_id', 'unknown')
            value = arguments.get('value', 'unknown')
            message = f"{timestamp} 🎛️  set_param plugin_{plugin_id}:p{param_id} {value}"

        elif tool_name.startswith('connect_'):
            message = f"{timestamp} 🔌 {tool_name.replace('_', ' ')}"

        elif 'session' in tool_name:
            action = tool_name.replace('_session', '').replace('session_', '')
            message = f"{timestamp} 💾 {action} session"

        else:
            # Generic command format
            message = f"{timestamp} ⌨️  {tool_name}"

        self._append_to_stream(message)

    async def _handle_plugin_event(self, event: Dict[str, Any]):
        """Handle plugin addition/removal events"""
        timestamp = self._format_military_time()
        action = event.get('action')
        plugin_id = event.get('plugin_id', 'unknown')
        value_str = event.get('value_str', 'unknown')

        if action == 'CALLBACK_PLUGIN_ADDED':
            message = f"{timestamp} ➕ plugin loaded: {value_str}"
        elif action == 'CALLBACK_PLUGIN_REMOVED':
            message = f"{timestamp} ➖ plugin unloaded: {value_str}"
        else:
            return

        self._append_to_stream(message)

    async def _handle_connection_event(self, event: Dict[str, Any]):
        """Handle audio/MIDI connection events"""
        timestamp = self._format_military_time()
        action = event.get('action')

        if action == 'CALLBACK_PATCHBAY_CONNECTION_ADDED':
            message = f"{timestamp} 🔌 new connection"
        elif action == 'CALLBACK_PATCHBAY_CONNECTION_REMOVED':
            message = f"{timestamp} ❌ connection removed"
        else:
            return

        self._append_to_stream(message)

    async def _handle_transport_event(self, event: Dict[str, Any]):
        """Handle transport state changes (play/stop/record)"""
        timestamp = self._format_military_time()

        # Get current transport info from Carla
        try:
            is_playing = self.carla.host.get_current_transport_frame() > 0
            # Note: Carla doesn't easily expose recording state, so we'll track it manually

            if is_playing and not self.transport_state['is_playing']:
                # Started playing
                self.transport_state['is_playing'] = True
                self.transport_state['start_time'] = datetime.now()
                message = f"{timestamp} ▶️  playback started"

            elif not is_playing and self.transport_state['is_playing']:
                # Stopped playing
                if self.transport_state['start_time']:
                    duration = (datetime.now() - self.transport_state['start_time']).total_seconds()
                    timecode = self._format_protools_timecode(duration)
                    message = f"{timestamp} ⏹️  playback stopped {timecode}"
                else:
                    message = f"{timestamp} ⏹️  playback stopped"

                self.transport_state['is_playing'] = False
                self.transport_state['start_time'] = None
            else:
                return

        except Exception as e:
            logger.error(f"Error handling transport event: {e}")
            return

        self._append_to_stream(message)

    async def _handle_parameter_event(self, event: Dict[str, Any]):
        """Handle parameter value changes (only significant ones)"""
        # Only log "significant" parameter changes to avoid spam
        # Could implement threshold logic here
        pass

    async def _handle_engine_event(self, event: Dict[str, Any]):
        """Handle engine events like clipping"""
        timestamp = self._format_military_time()
        action = event.get('action')

        # Handle clipping/overload events
        if 'OVERLOAD' in str(action) or 'CLIP' in str(action):
            plugin_id = event.get('plugin_id', 'unknown')
            message = f"{timestamp} ⚠️  clipping plugin_{plugin_id}"
            self._append_to_stream(message)

    def log_recording_start(self):
        """Manually log recording start (called externally)"""
        timestamp = self._format_military_time()
        self.transport_state['is_recording'] = True
        self.transport_state['start_time'] = datetime.now()
        message = f"{timestamp} ⏺️  recording started"
        self._append_to_stream(message)

    def log_recording_stop(self):
        """Manually log recording stop (called externally)"""
        timestamp = self._format_military_time()

        if self.transport_state['start_time']:
            duration = (datetime.now() - self.transport_state['start_time']).total_seconds()
            timecode = self._format_protools_timecode(duration)
            message = f"{timestamp} ⏹️  recording stopped {timecode}"
        else:
            message = f"{timestamp} ⏹️  recording stopped"

        self.transport_state['is_recording'] = False
        self.transport_state['start_time'] = None
        self._append_to_stream(message)

    def close(self):
        """Clean shutdown of stream logger"""
        timestamp = self._format_military_time()
        message = f"{timestamp} 🔚 session ended"
        self._append_to_stream(message)
        logger.info("AmbientStreamLogger closed")