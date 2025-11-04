#!/usr/bin/env python3
"""
Audio Analysis Tools for Carla MCP Server
"""

import logging
import numpy as np
import time
import asyncio
from typing import Dict, Any, List, Optional, Union
from scipy import signal

logger = logging.getLogger(__name__)


class AnalysisTools:
    """Audio analysis tools for Carla"""
    
    def __init__(self, carla_controller):
        """Initialize analysis tools
        
        Args:
            carla_controller: CarlaController instance
        """
        self.carla = carla_controller
        self.analysis_cache = {}
        
        logger.info("AnalysisTools initialized")
    
    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Execute an analysis tool
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            
        Returns:
            Tool execution result
        """
        if tool_name == "analyze_spectrum":
            return await self.analyze_spectrum(**arguments)
        elif tool_name == "measure_levels":
            return await self.measure_levels(**arguments)
        elif tool_name == "capture_plugin_parameters":
            return await self.capture_plugin_parameters(**arguments)
        elif tool_name == "detect_feedback":
            return await self.detect_feedback(**arguments)
        elif tool_name == "analyze_latency":
            return await self.analyze_latency(**arguments)
        else:
            raise ValueError(f"Unknown analysis tool: {tool_name}")
    
    async def analyze_spectrum(self, source: str, fft_size: int = 2048,
                              window: str = "hann", session_context: dict = None, **kwargs) -> dict:
        """Perform spectrum analysis
        
        NOTE: Carla's Python API does not provide direct access to audio buffers for FFT analysis.
        This returns real peak data instead of spectrum data.
        
        Args:
            source: Plugin ID or bus ID
            fft_size: FFT size (512-8192) - kept for API compatibility
            window: Window function - kept for API compatibility
            
        Returns:
            Available audio analysis data
        """
        try:
            plugin_id = int(source) if source.isdigit() else None
            
            if plugin_id is None or plugin_id not in self.carla.plugins:
                raise ValueError(f"Invalid plugin ID: {source}")
            
            # Get REAL audio peaks from Carla
            peaks = self.carla.get_audio_peaks(plugin_id)
            
            # Get REAL sample rate
            sample_rate = self.carla.host.get_sample_rate() if self.carla.engine_running else 48000
            
            # Calculate approximate frequency response based on peaks
            # This is the best we can do without direct buffer access
            peak_avg = (peaks['out_left'] + peaks['out_right']) / 2.0
            
            return {
                'success': True,
                'source': source,
                'note': 'Carla Python API does not expose audio buffers for FFT. Using peak analysis instead.',
                'sample_rate': sample_rate,
                'peaks': peaks,
                'peak_average': peak_avg,
                'peak_db': 20 * np.log10(peak_avg + 1e-10),
                'fft_size': fft_size,  # Kept for compatibility
                'window': window  # Kept for compatibility
            }
            
        except Exception as e:
            logger.error(f"Failed to analyze audio: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def measure_levels(self, source: str, window_ms: int = 100,
                           include_history: bool = False, 
                           capture_duration_ms: Optional[int] = None,
                           silence_threshold_db: Optional[float] = None,
                           session_context: dict = None, **kwargs) -> dict:
        """Measure REAL audio levels using Carla API
        
        Args:
            source: Plugin ID or bus ID
            window_ms: Measurement window in milliseconds (for history collection)
            include_history: Include historical data
            capture_duration_ms: Capture for this many milliseconds (e.g., 60000 for 1 minute)
            silence_threshold_db: Stop capturing when level drops below this (e.g., -50)
            
        Returns:
            REAL audio level measurements from Carla
        """
        try:
            plugin_id = int(source) if source.isdigit() else None
            
            if plugin_id is None or plugin_id not in self.carla.plugins:
                raise ValueError(f"Invalid plugin ID: {source}")
            
            # Get REAL peaks from Carla API
            peaks = self.carla.get_audio_peaks(plugin_id)
            
            # Calculate REAL peak levels in dB
            peak_left_db = 20 * np.log10(peaks['out_left'] + 1e-10)
            peak_right_db = 20 * np.log10(peaks['out_right'] + 1e-10)
            peak_max_db = max(peak_left_db, peak_right_db)
            
            # Calculate stereo balance from real peaks
            if peaks['out_left'] + peaks['out_right'] > 0:
                balance = (peaks['out_right'] - peaks['out_left']) / (peaks['out_right'] + peaks['out_left'])
            else:
                balance = 0.0
            
            result = {
                'success': True,
                'source': source,
                'plugin_name': self.carla.plugins[plugin_id]['name'],
                'peak_left_db': float(peak_left_db),
                'peak_right_db': float(peak_right_db),
                'peak_max_db': float(peak_max_db),
                'balance': float(balance),
                'peaks_raw': peaks,
                'input_peaks': {
                    'left': peaks['in_left'],
                    'right': peaks['in_right'],
                    'left_db': 20 * np.log10(peaks['in_left'] + 1e-10),
                    'right_db': 20 * np.log10(peaks['in_right'] + 1e-10)
                },
                'output_peaks': {
                    'left': peaks['out_left'],
                    'right': peaks['out_right'],
                    'left_db': peak_left_db,
                    'right_db': peak_right_db
                }
            }
            
            if include_history or capture_duration_ms or silence_threshold_db:
                # Collect REAL peak history over time
                import time
                history = []
                
                # Determine sampling interval - adaptive with 250ms minimum
                if capture_duration_ms and int(capture_duration_ms) > 30000:
                    interval_ms = 5000  # 5 seconds for very long captures
                elif capture_duration_ms and int(capture_duration_ms) > 10000:
                    interval_ms = 1000  # 1 second for medium captures (10-30s)
                elif capture_duration_ms:
                    # For captures under 10 seconds, use 250ms minimum
                    interval_ms = 250  # 250ms minimum as required
                else:
                    interval_ms = 10  # Default for simple mode

                # Determine capture mode and duration
                if capture_duration_ms:
                    # Duration-based capture - calculate samples from interval
                    samples = int(capture_duration_ms) // interval_ms
                    capture_mode = "duration"
                elif silence_threshold_db is not None:
                    # Threshold-based capture (max 60 seconds)
                    samples = 60000 // interval_ms  # Max based on interval
                    capture_mode = "threshold"
                    consecutive_below = 0
                else:
                    # Simple history mode
                    samples = min(10, window_ms // interval_ms)
                    capture_mode = "simple"
                
                # Capture loop - wrap blocking calls in thread pool
                async def capture_peak_sample(i):
                    """Capture a single peak sample in thread pool"""
                    def _capture():
                        snapshot = self.carla.get_audio_peaks(plugin_id)
                        current_peak_db = max(
                            20 * np.log10(snapshot['out_left'] + 1e-10),
                            20 * np.log10(snapshot['out_right'] + 1e-10)
                        )
                        return snapshot, current_peak_db

                    return await asyncio.to_thread(_capture)

                for i in range(samples):
                    snapshot, current_peak_db = await capture_peak_sample(i)

                    history.append({
                        'time_ms': i * interval_ms,
                        'out_left': snapshot['out_left'],
                        'out_right': snapshot['out_right'],
                        'in_left': snapshot['in_left'],
                        'in_right': snapshot['in_right'],
                        'peak_db': current_peak_db
                    })

                    # Check threshold stop condition
                    if capture_mode == "threshold" and silence_threshold_db is not None:
                        if current_peak_db < silence_threshold_db:
                            consecutive_below += 1
                            if consecutive_below >= 5:  # 50ms of silence
                                result['capture_stopped_reason'] = f"Signal below {silence_threshold_db} dB"
                                result['capture_duration_ms'] = i * 10
                                break
                        else:
                            consecutive_below = 0

                    # Yield control to event loop
                    await asyncio.sleep(interval_ms / 1000.0)
                
                result['history'] = history
                result['capture_mode'] = capture_mode
                
                if capture_duration_ms:
                    result['capture_duration_ms'] = len(history) * 10
            
            return result
                
        except Exception as e:
            logger.error(f"Failed to measure levels: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def capture_plugin_parameters(self, plugin_ids: Union[int, List[int]],
                                       capture_duration_ms: int = 10000,
                                       sampling_interval_ms: int = 100,
                                       session_context: dict = None, **kwargs) -> dict:
        """
        Capture all parameter values from one or more plugins over time

        Args:
            plugin_ids: Single plugin ID or list of plugin IDs to capture
            capture_duration_ms: Total duration to capture (default 10 seconds)
            sampling_interval_ms: Time between samples (default 100ms)

        Returns:
            Dictionary with parameter history for each plugin
        """
        try:
            # Handle string conversion from MCP
            if isinstance(plugin_ids, str):
                # Check if it's a string representation of a list
                if plugin_ids.startswith('['):
                    import json
                    plugin_ids = json.loads(plugin_ids)
                else:
                    plugin_ids = int(plugin_ids)

            # Ensure plugin_ids is a list
            if isinstance(plugin_ids, int):
                plugin_ids = [plugin_ids]

            # Validate all plugin IDs exist
            for pid in plugin_ids:
                if pid not in self.carla.plugins:
                    raise ValueError(f"Invalid plugin ID: {pid}")

            # Filter out plugins with no capturable data
            skipped_plugins = []
            capturable_plugins = []

            for pid in plugin_ids:
                param_count = self.carla.host.get_parameter_count(pid)
                plugin_name = self.carla.plugins[pid]['name']

                if param_count == 0:
                    # No parameters at all
                    skipped_plugins.append({
                        'id': pid,
                        'name': plugin_name,
                        'reason': 'No parameters - pure visualization plugin'
                    })
                    continue

                # Check for OUTPUT parameters (type=2)
                has_output_params = False
                for i in range(param_count):
                    data = self.carla.host.get_parameter_data(pid, i)
                    if data and data.get('type', 0) == 2:  # PARAMETER_OUTPUT
                        has_output_params = True
                        break

                if not has_output_params:
                    # Only INPUT parameters - no meter data to capture
                    skipped_plugins.append({
                        'id': pid,
                        'name': plugin_name,
                        'reason': f'No OUTPUT parameters - only {param_count} control parameters (no meter data)'
                    })
                else:
                    capturable_plugins.append(pid)

            # Use filtered list
            plugin_ids = capturable_plugins

            import time

            # Calculate number of samples
            samples = int(capture_duration_ms / sampling_interval_ms)

            # Initialize result structure
            result = {
                'success': True,
                'capture_duration_ms': capture_duration_ms,
                'sampling_interval_ms': sampling_interval_ms,
                'samples': samples,
                'plugins': {},
                'skipped_plugins': skipped_plugins,
                'capturable_count': len(plugin_ids),
                'skipped_count': len(skipped_plugins)
            }

            # Early exit if nothing to capture
            if len(plugin_ids) == 0:
                result['success'] = False
                result['error'] = 'All plugins skipped - no capturable meter data (need OUTPUT parameters)'
                return result

            # Initialize plugin data structures
            for pid in plugin_ids:
                plugin_info = self.carla.plugins[pid]
                param_count = self.carla.host.get_parameter_count(pid)
                
                # Get parameter info for all parameters
                param_info = []
                for i in range(param_count):
                    info = self.carla.host.get_parameter_info(pid, i)
                    data = self.carla.host.get_parameter_data(pid, i)
                    ranges = self.carla.host.get_parameter_ranges(pid, i)
                    
                    param_info.append({
                        'index': i,
                        'name': info['name'] if info else f'param_{i}',
                        'symbol': info['symbol'] if info else '',
                        'unit': info['unit'] if info else '',
                        'min': ranges['min'] if ranges else 0.0,
                        'max': ranges['max'] if ranges else 1.0
                    })
                
                result['plugins'][pid] = {
                    'name': plugin_info['name'],
                    'parameters': param_info,
                    'history': []
                }
            
            # Capture loop - run in thread pool to avoid blocking
            async def capture_sample(sample_idx):
                """Capture a single sample in thread pool"""
                def _capture():
                    sample_time = sample_idx * sampling_interval_ms
                    samples_data = {}

                    # Capture all parameters for all plugins
                    for pid in plugin_ids:
                        param_count = self.carla.host.get_parameter_count(pid)
                        sample_data = {
                            'time_ms': sample_time,
                            'values': {}
                        }

                        # Read all parameter values
                        for param_idx in range(param_count):
                            value = self.carla.host.get_current_parameter_value(pid, param_idx)
                            param_name = result['plugins'][pid]['parameters'][param_idx]['name']
                            sample_data['values'][param_name] = value
                            # Also store by index for easier access
                            sample_data['values'][f'param_{param_idx}'] = value

                        samples_data[pid] = sample_data

                    return samples_data

                # Run in thread pool to avoid blocking event loop
                return await asyncio.to_thread(_capture)

            # Capture all samples
            for sample_idx in range(samples):
                samples_data = await capture_sample(sample_idx)

                # Store results
                for pid, sample_data in samples_data.items():
                    result['plugins'][pid]['history'].append(sample_data)

                # Sleep between samples
                if sample_idx < samples - 1:
                    await asyncio.sleep(sampling_interval_ms / 1000.0)
            
            # Add summary statistics for each plugin
            for pid in plugin_ids:
                history = result['plugins'][pid]['history']
                if history:
                    # Calculate min/max/avg for each parameter
                    param_stats = {}
                    for param in result['plugins'][pid]['parameters']:
                        param_name = param['name']
                        values = [h['values'][param_name] for h in history]
                        param_stats[param_name] = {
                            'min': min(values),
                            'max': max(values),
                            'avg': sum(values) / len(values),
                            'final': values[-1]
                        }
                    result['plugins'][pid]['statistics'] = param_stats
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to capture plugin parameters: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def detect_feedback(self, sensitivity: float = 0.8,
                            session_context: dict = None, **kwargs) -> dict:
        """Detect feedback loops by analyzing REAL routing connections
        
        Args:
            sensitivity: Detection sensitivity (0-1)
            
        Returns:
            REAL feedback detection based on actual routing
        """
        try:
            feedback_points = []
            risky_connections = []
            
            # Analyze REAL connections for feedback loops
            connections = self.carla.connections
            
            # Build connection graph to detect cycles
            graph = {}
            for conn in connections:
                src = f"{conn['source']['plugin']}:{conn['source']['port']}"
                dst = f"{conn['dest']['plugin']}:{conn['dest']['port']}"
                
                if conn['source']['plugin'] not in graph:
                    graph[conn['source']['plugin']] = []
                graph[conn['source']['plugin']].append(conn['dest']['plugin'])
            
            # Detect cycles in the graph (real feedback loops)
            def has_cycle(node, visited, rec_stack):
                visited[node] = True
                rec_stack[node] = True
                
                if node in graph:
                    for neighbor in graph[node]:
                        if neighbor not in visited:
                            visited[neighbor] = False
                            rec_stack[neighbor] = False
                        
                        if not visited[neighbor]:
                            if has_cycle(neighbor, visited, rec_stack):
                                return True
                        elif rec_stack[neighbor]:
                            return True
                
                rec_stack[node] = False
                return False
            
            # Check each plugin for cycles
            for plugin_id in self.carla.plugins:
                visited = {}
                rec_stack = {}
                
                for p in self.carla.plugins:
                    visited[p] = False
                    rec_stack[p] = False
                
                if has_cycle(plugin_id, visited, rec_stack):
                    # Get REAL peak values to check for actual feedback
                    peaks = self.carla.get_audio_peaks(plugin_id)
                    peak_avg = (peaks['out_left'] + peaks['out_right']) / 2.0
                    
                    # High peaks might indicate feedback
                    if peak_avg > sensitivity:
                        feedback_points.append({
                            'plugin_id': plugin_id,
                            'plugin_name': self.carla.plugins[plugin_id]['name'],
                            'peak_level': float(peak_avg),
                            'risk_level': 'high' if peak_avg > 0.9 else 'medium'
                        })
            
            # Check for risky parallel connections
            for conn in connections:
                # Check if output level is high
                if conn['source']['plugin'] in self.carla.plugins:
                    peaks = self.carla.get_audio_peaks(conn['source']['plugin'])
                    if peaks['out_left'] > sensitivity or peaks['out_right'] > sensitivity:
                        risky_connections.append({
                            'source': conn['source']['plugin'],
                            'dest': conn['dest']['plugin'],
                            'peak_level': max(peaks['out_left'], peaks['out_right'])
                        })
            
            return {
                'success': True,
                'feedback_detected': len(feedback_points) > 0,
                'feedback_points': feedback_points,
                'risky_connections': risky_connections,
                'total_connections': len(connections),
                'sensitivity': sensitivity,
                'plugins_analyzed': len(self.carla.plugins)
            }
            
        except Exception as e:
            logger.error(f"Failed to detect feedback: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def analyze_latency(self, measure_plugins: bool = True,
                            measure_hardware: bool = True,
                            session_context: dict = None, **kwargs) -> dict:
        """Measure REAL system and hardware latencies from Carla
        
        NOTE: Carla Python API does not expose per-plugin latency.
        
        Args:
            measure_plugins: Measure plugin latencies (limited by API)
            measure_hardware: Measure hardware latency
            
        Returns:
            REAL latency measurements from Carla
        """
        try:
            total_latency = 0
            hardware_latency = 0
            buffer_size = 0
            sample_rate = 48000  # Default
            
            if self.carla.engine_running:
                # Get REAL buffer size and sample rate from Carla
                buffer_size = self.carla.host.get_buffer_size()
                sample_rate = self.carla.host.get_sample_rate()
                
                if measure_hardware:
                    # Calculate REAL hardware latency
                    hardware_latency = (buffer_size / sample_rate) * 1000  # ms
                    total_latency = hardware_latency
                    
                    # Account for JACK periods if using JACK
                    # Default JACK uses 2 periods
                    periods = 2
                    total_hardware_latency = hardware_latency * periods
            
            # Plugin latency info
            plugin_info = {
                'note': 'Carla Python API does not expose individual plugin latency values.',
                'plugin_count': len(self.carla.plugins),
                'plugins': []
            }
            
            if measure_plugins:
                # We can at least list the plugins and their processing state
                for plugin_id, plugin_data in self.carla.plugins.items():
                    info = self.carla.host.get_plugin_info(plugin_id)
                    plugin_info['plugins'].append({
                        'id': plugin_id,
                        'name': plugin_data['name'],
                        'active': plugin_data['active'],
                        'type': info.get('label', 'Unknown') if info else 'Unknown'
                    })
            
            return {
                'success': True,
                'hardware_latency_ms': hardware_latency,
                'total_hardware_latency_ms': total_hardware_latency if measure_hardware else hardware_latency,
                'buffer_size_samples': buffer_size,
                'sample_rate_hz': sample_rate,
                'latency_samples': buffer_size,
                'engine_running': self.carla.engine_running,
                'plugin_info': plugin_info,
                'note': 'Individual plugin latencies not available via Carla Python API'
            }
            
        except Exception as e:
            logger.error(f"Failed to analyze latency: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }