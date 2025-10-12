#!/usr/bin/env python3
"""
Audio Monitor for Carla MCP Server
"""

import logging
import time
import threading
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class AudioMonitor:
    """Real-time audio monitoring for Carla"""
    
    def __init__(self, carla_controller):
        """Initialize audio monitor
        
        Args:
            carla_controller: CarlaController instance
        """
        self.carla = carla_controller
        self.monitoring = False
        self.monitor_thread = None
        self.meter_callbacks = []
        self.peak_history = {}
        
        logger.info("AudioMonitor initialized")
    
    def start_monitoring(self, rate_hz: int = 30):
        """Start audio monitoring
        
        Args:
            rate_hz: Update rate in Hz
        """
        if self.monitoring:
            return
        
        self.monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(rate_hz,),
            daemon=True
        )
        self.monitor_thread.start()
        logger.info(f"Started audio monitoring at {rate_hz}Hz")
    
    def stop_monitoring(self):
        """Stop audio monitoring"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=1.0)
        logger.info("Stopped audio monitoring")
    
    def _monitor_loop(self, rate_hz: int):
        """Monitor loop running in thread
        
        Args:
            rate_hz: Update rate in Hz
        """
        interval = 1.0 / rate_hz
        
        while self.monitoring:
            try:
                # Get peaks for all plugins
                for plugin_id in self.carla.plugins:
                    peaks = self.carla.get_audio_peaks(plugin_id)
                    
                    # Store in history
                    if plugin_id not in self.peak_history:
                        self.peak_history[plugin_id] = []
                    
                    self.peak_history[plugin_id].append({
                        'timestamp': time.time(),
                        'peaks': peaks
                    })
                    
                    # Limit history size
                    if len(self.peak_history[plugin_id]) > 100:
                        self.peak_history[plugin_id].pop(0)
                    
                    # Call callbacks
                    for callback in self.meter_callbacks:
                        callback(plugin_id, peaks)
                
                time.sleep(interval)
                
            except Exception as e:
                logger.error(f"Monitor loop error: {str(e)}")
    
    def subscribe_meters(self, callback: callable):
        """Subscribe to meter updates
        
        Args:
            callback: Function to call with meter data
        """
        self.meter_callbacks.append(callback)
    
    def get_peak_history(self, plugin_id: int) -> List[dict]:
        """Get peak history for a plugin
        
        Args:
            plugin_id: Plugin ID
            
        Returns:
            List of peak measurements
        """
        return self.peak_history.get(plugin_id, [])