#!/usr/bin/env python3
"""
CPU Monitor for Carla MCP Server
"""

import logging
import psutil
import time
import threading
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class CPUMonitor:
    """CPU usage monitoring for Carla"""
    
    def __init__(self, carla_controller):
        """Initialize CPU monitor
        
        Args:
            carla_controller: CarlaController instance
        """
        self.carla = carla_controller
        self.monitoring = False
        self.monitor_thread = None
        self.cpu_history = []
        self.plugin_loads = {}
        
        logger.info("CPUMonitor initialized")
    
    def start_monitoring(self, interval: float = 1.0):
        """Start CPU monitoring
        
        Args:
            interval: Update interval in seconds
        """
        if self.monitoring:
            return
        
        self.monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True
        )
        self.monitor_thread.start()
        logger.info(f"Started CPU monitoring with {interval}s interval")
    
    def stop_monitoring(self):
        """Stop CPU monitoring"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2.0)
        logger.info("Stopped CPU monitoring")
    
    def _monitor_loop(self, interval: float):
        """Monitor loop running in thread
        
        Args:
            interval: Update interval in seconds
        """
        while self.monitoring:
            try:
                # Get system CPU usage
                cpu_percent = psutil.cpu_percent(interval=0.1)
                
                # Get memory usage
                memory = psutil.virtual_memory()
                
                # Get plugin CPU loads
                total_plugin_load = 0
                for plugin_id in self.carla.plugins:
                    load = self.carla.get_cpu_load(plugin_id)
                    self.plugin_loads[plugin_id] = load
                    total_plugin_load += load
                
                # Store in history
                self.cpu_history.append({
                    'timestamp': time.time(),
                    'system_cpu': cpu_percent,
                    'memory_percent': memory.percent,
                    'plugin_load': total_plugin_load
                })
                
                # Limit history size
                if len(self.cpu_history) > 300:  # 5 minutes at 1s interval
                    self.cpu_history.pop(0)
                
                time.sleep(interval)
                
            except Exception as e:
                logger.error(f"CPU monitor error: {str(e)}")
    
    def get_current_usage(self) -> float:
        """Get current CPU usage
        
        Returns:
            Current CPU percentage
        """
        if self.cpu_history:
            return self.cpu_history[-1]['system_cpu']
        return psutil.cpu_percent(interval=0.1)
    
    def get_plugin_loads(self) -> Dict[int, float]:
        """Get CPU load for each plugin
        
        Returns:
            Dictionary of plugin loads
        """
        return self.plugin_loads.copy()
    
    def get_history(self, duration: int = 60) -> List[dict]:
        """Get CPU history
        
        Args:
            duration: Duration in seconds
            
        Returns:
            List of CPU measurements
        """
        if not self.cpu_history:
            return []
        
        current_time = time.time()
        return [
            entry for entry in self.cpu_history
            if current_time - entry['timestamp'] <= duration
        ]