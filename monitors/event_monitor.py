#!/usr/bin/env python3
"""
Event Monitor for Carla MCP Server
"""

import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Callable

logger = logging.getLogger(__name__)


class EventMonitor:
    """Real-time event monitoring for Carla"""
    
    def __init__(self, carla_controller):
        """Initialize event monitor
        
        Args:
            carla_controller: CarlaController instance
        """
        self.carla = carla_controller
        self.event_handlers = {}
        self.event_history = []
        self.max_history = 1000
        
        logger.info("EventMonitor initialized")
    
    async def handle_event(self, event: dict):
        """Handle incoming Carla event
        
        Args:
            event: Event data from Carla callback
        """
        # Add timestamp if not present
        if 'timestamp' not in event:
            event['timestamp'] = datetime.now().isoformat()
        
        # Store in history
        self.event_history.append(event)
        if len(self.event_history) > self.max_history:
            self.event_history.pop(0)
        
        # Call registered handlers
        action = event.get('action')
        if action in self.event_handlers:
            for handler in self.event_handlers[action]:
                try:
                    await handler(event)
                except Exception as e:
                    logger.error(f"Event handler error: {str(e)}")
    
    def subscribe(self, event_type: str, handler: Callable):
        """Subscribe to specific event type
        
        Args:
            event_type: Type of event to subscribe to
            handler: Callback function
        """
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        self.event_handlers[event_type].append(handler)
        logger.info(f"Subscribed to event type: {event_type}")
    
    def get_recent_events(self, count: int = 10) -> List[dict]:
        """Get recent events
        
        Args:
            count: Number of events to return
            
        Returns:
            List of recent events
        """
        return self.event_history[-count:]