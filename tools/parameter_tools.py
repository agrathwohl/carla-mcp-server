#!/usr/bin/env python3
"""
Parameter Automation Tools for Carla MCP Server
"""

import logging
import time
import math
import random
import threading
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import uuid
from base_tools import validate_plugin_id, validate_parameter_id

logger = logging.getLogger(__name__)


class ParameterTools:
    """Parameter automation and control tools for Carla"""
    
    def __init__(self, carla_controller):
        """Initialize parameter tools
        
        Args:
            carla_controller: CarlaController instance
        """
        self.carla = carla_controller
        self.automations = {}
        self.macros = {}
        self.midi_mappings = {}
        self.recording_sessions = {}
        
        logger.info("ParameterTools initialized")
    
    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Execute a parameter tool
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            
        Returns:
            Tool execution result
        """
        if tool_name == "automate_parameter":
            return await self.automate_parameter(**arguments)
        elif tool_name == "map_midi_cc":
            return await self.map_midi_cc(**arguments)
        elif tool_name == "create_macro":
            return await self.create_macro(**arguments)
        elif tool_name == "record_automation":
            return await self.record_automation(**arguments)
        elif tool_name == "set_parameter":
            return await self.set_parameter(**arguments)
        elif tool_name == "get_parameter":
            return await self.get_parameter(**arguments)
        elif tool_name == "randomize_parameters":
            return await self.randomize_parameters(**arguments)
        elif tool_name == "morph_parameters":
            return await self.morph_parameters(**arguments)
        else:
            raise ValueError(f"Unknown parameter tool: {tool_name}")
    
    async def automate_parameter(self, plugin_id: str, parameter_id: int,
                                automation_type: str, duration_ms: int,
                                values: Optional[List[float]] = None,
                                session_context: dict = None, **kwargs) -> dict:
        """Create parameter automation
        
        Args:
            plugin_id: Plugin ID
            parameter_id: Parameter index
            automation_type: Type of automation (linear, exponential, sine, random_walk)
            duration_ms: Duration in milliseconds
            values: Keyframe values (optional)
            
        Returns:
            Automation information
        """
        try:
            plugin_id = validate_plugin_id(plugin_id, self.carla)

            # Get parameter info
            param_info = self.carla.get_parameter_info(plugin_id, parameter_id)

            # Generate automation ID
            automation_id = str(uuid.uuid4())
            
            # Create automation thread
            automation_thread = threading.Thread(
                target=self._run_automation,
                args=(plugin_id, parameter_id, automation_type, duration_ms, values, param_info)
            )
            
            # Store automation info
            self.automations[automation_id] = {
                'id': automation_id,
                'plugin_id': plugin_id,
                'parameter_id': parameter_id,
                'type': automation_type,
                'duration_ms': duration_ms,
                'values': values,
                'thread': automation_thread,
                'running': True,
                'start_time': time.time()
            }
            
            # Start automation
            automation_thread.start()
            
            logger.info(f"Started {automation_type} automation for plugin {plugin_id} param {parameter_id}")
            
            # Calculate actual values that will be applied
            actual_values = self._calculate_automation_values(
                automation_type, duration_ms, values, param_info
            )
            
            return {
                'success': True,
                'automation_id': automation_id,
                'plugin_id': plugin_id,
                'parameter_id': parameter_id,
                'parameter_name': param_info['name'],
                'automation_type': automation_type,
                'duration_ms': duration_ms,
                'actual_values': actual_values[:10] if len(actual_values) > 10 else actual_values  # First 10 values
            }
            
        except Exception as e:
            logger.error(f"Failed to automate parameter: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _run_automation(self, plugin_id: int, parameter_id: int, automation_type: str,
                       duration_ms: int, values: Optional[List[float]], param_info: dict):
        """Run parameter automation in thread
        
        Args:
            plugin_id: Plugin ID
            parameter_id: Parameter index
            automation_type: Type of automation
            duration_ms: Duration in milliseconds
            values: Keyframe values
            param_info: Parameter information
        """
        try:
            start_time = time.time()
            steps = int(duration_ms / 10)  # 10ms resolution
            
            if automation_type == "linear":
                if values and len(values) >= 2:
                    start_val = values[0]
                    end_val = values[1]
                else:
                    start_val = param_info['current']
                    end_val = param_info['max']
                
                for i in range(steps):
                    progress = i / steps
                    value = start_val + (end_val - start_val) * progress
                    self.carla.set_parameter(plugin_id, parameter_id, value)
                    time.sleep(0.01)
                    
            elif automation_type == "exponential":
                if values and len(values) >= 2:
                    start_val = values[0]
                    end_val = values[1]
                else:
                    start_val = param_info['min']
                    end_val = param_info['max']
                
                for i in range(steps):
                    progress = i / steps
                    value = start_val + (end_val - start_val) * (progress ** 2)
                    self.carla.set_parameter(plugin_id, parameter_id, value)
                    time.sleep(0.01)
                    
            elif automation_type == "sine":
                if values and len(values) >= 2:
                    min_val = values[0]
                    max_val = values[1]
                    frequency = values[2] if len(values) > 2 else 1.0
                else:
                    min_val = param_info['min']
                    max_val = param_info['max']
                    frequency = 1.0
                
                for i in range(steps):
                    t = i / 100.0  # Time in seconds
                    value = min_val + (max_val - min_val) * (0.5 + 0.5 * math.sin(2 * math.pi * frequency * t))
                    self.carla.set_parameter(plugin_id, parameter_id, value)
                    time.sleep(0.01)
                    
            elif automation_type == "random_walk":
                current_val = param_info['current']
                min_val = param_info['min']
                max_val = param_info['max']
                step_size = (max_val - min_val) * 0.05  # 5% step size
                
                for i in range(steps):
                    # Random walk
                    delta = random.uniform(-step_size, step_size)
                    current_val = max(min_val, min(max_val, current_val + delta))
                    self.carla.set_parameter(plugin_id, parameter_id, current_val)
                    time.sleep(0.01)
            
            logger.info(f"Completed automation for plugin {plugin_id} param {parameter_id}")
            
        except Exception as e:
            logger.error(f"Automation error: {str(e)}")
    
    def _calculate_automation_values(self, automation_type: str, duration_ms: int,
                                    values: Optional[List[float]], param_info: dict) -> List[float]:
        """Calculate automation values for preview
        
        Args:
            automation_type: Type of automation
            duration_ms: Duration in milliseconds
            values: Keyframe values
            param_info: Parameter information
            
        Returns:
            List of calculated values
        """
        steps = min(100, int(duration_ms / 10))  # Limit to 100 values for preview
        calculated_values = []
        
        if automation_type == "linear":
            start_val = values[0] if values else param_info['current']
            end_val = values[1] if values and len(values) > 1 else param_info['max']
            
            for i in range(steps):
                progress = i / steps
                value = start_val + (end_val - start_val) * progress
                calculated_values.append(round(value, 3))
                
        elif automation_type == "sine":
            min_val = values[0] if values else param_info['min']
            max_val = values[1] if values and len(values) > 1 else param_info['max']
            frequency = values[2] if values and len(values) > 2 else 1.0
            
            for i in range(steps):
                t = i * duration_ms / (steps * 1000.0)
                value = min_val + (max_val - min_val) * (0.5 + 0.5 * math.sin(2 * math.pi * frequency * t))
                calculated_values.append(round(value, 3))
        
        return calculated_values
    
    async def map_midi_cc(self, plugin_id: str, parameter_id: int, cc_number: int,
                        channel: int = 1, range: Optional[dict] = None,
                        curve: str = "linear", session_context: dict = None, **kwargs) -> dict:
        """Map MIDI CC to parameter
        
        Args:
            plugin_id: Plugin ID
            parameter_id: Parameter index
            cc_number: MIDI CC number (0-127)
            channel: MIDI channel (1-16)
            range: Value range mapping
            curve: Mapping curve (linear, exponential, logarithmic)
            
        Returns:
            Mapping information
        """
        try:
            plugin_id = validate_plugin_id(plugin_id, self.carla)

            # Validate CC number
            if cc_number < 0 or cc_number > 127:
                raise ValueError("CC number must be between 0 and 127")
            
            # Validate channel
            if channel < 1 or channel > 16:
                raise ValueError("MIDI channel must be between 1 and 16")
            
            # Map the CC
            self.carla.map_midi_cc(plugin_id, parameter_id, cc_number, channel - 1)
            
            # Generate mapping ID
            mapping_id = str(uuid.uuid4())
            
            # Get parameter info
            param_info = self.carla.get_parameter_info(plugin_id, parameter_id)
            
            # Store mapping info
            self.midi_mappings[mapping_id] = {
                'id': mapping_id,
                'plugin_id': plugin_id,
                'parameter_id': parameter_id,
                'parameter_name': param_info['name'],
                'cc_number': cc_number,
                'channel': channel,
                'range': range or {'min': param_info['min'], 'max': param_info['max']},
                'curve': curve,
                'current_value': param_info['current']
            }
            
            logger.info(f"Mapped CC {cc_number} ch {channel} to plugin {plugin_id} param {parameter_id}")
            
            return {
                'success': True,
                'mapping_id': mapping_id,
                'plugin_id': plugin_id,
                'parameter_id': parameter_id,
                'parameter_name': param_info['name'],
                'cc_number': cc_number,
                'channel': channel,
                'range': self.midi_mappings[mapping_id]['range'],
                'curve': curve,
                'current_value': param_info['current']
            }
            
        except Exception as e:
            logger.error(f"Failed to map MIDI CC: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def create_macro(self, name: str, targets: List[dict],
                         session_context: dict = None, **kwargs) -> dict:
        """Create macro control for multiple parameters
        
        Args:
            name: Macro name
            targets: List of target parameters with ranges and curves
            
        Returns:
            Macro information
        """
        try:
            # Generate macro ID
            macro_id = str(uuid.uuid4())
            
            # Process targets
            processed_targets = []
            affected_parameters = []
            
            for target in targets:
                plugin_id = int(target['plugin_id'])
                param_id = target['param_id']
                
                if plugin_id not in self.carla.plugins:
                    logger.warning(f"Plugin {plugin_id} not found, skipping")
                    continue
                
                # Get parameter info
                param_info = self.carla.get_parameter_info(plugin_id, param_id)
                
                processed_target = {
                    'plugin_id': plugin_id,
                    'param_id': param_id,
                    'param_name': param_info['name'],
                    'range': target.get('range', {'min': param_info['min'], 'max': param_info['max']}),
                    'curve': target.get('curve', 'linear'),
                    'inverted': target.get('inverted', False)
                }
                
                processed_targets.append(processed_target)
                affected_parameters.append(f"{plugin_id}:{param_id}")
            
            # Store macro
            self.macros[macro_id] = {
                'id': macro_id,
                'name': name,
                'targets': processed_targets,
                'value': 0.5  # Default to center position
            }
            
            logger.info(f"Created macro '{name}' controlling {len(processed_targets)} parameters")
            
            return {
                'success': True,
                'macro_id': macro_id,
                'name': name,
                'affected_parameters': affected_parameters,
                'target_count': len(processed_targets),
                'initial_value': 0.5
            }
            
        except Exception as e:
            logger.error(f"Failed to create macro: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def record_automation(self, plugin_id: str, parameters: List[int],
                              duration_ms: int, session_context: dict = None, **kwargs) -> dict:
        """Record parameter changes in real-time
        
        Args:
            plugin_id: Plugin ID
            parameters: List of parameter indices to record
            duration_ms: Recording duration in milliseconds
            
        Returns:
            Recorded automation data
        """
        try:
            plugin_id = validate_plugin_id(plugin_id, self.carla)

            # Generate recording ID
            recording_id = str(uuid.uuid4())
            
            # Initialize recording data
            recording_data = {
                'id': recording_id,
                'plugin_id': plugin_id,
                'parameters': {},
                'start_time': time.time(),
                'duration_ms': duration_ms
            }
            
            # Initialize parameter recording
            for param_id in parameters:
                recording_data['parameters'][param_id] = {
                    'values': [],
                    'timestamps': []
                }
            
            # Store recording session
            self.recording_sessions[recording_id] = recording_data
            
            # Start recording thread
            recording_thread = threading.Thread(
                target=self._record_parameters,
                args=(recording_id, plugin_id, parameters, duration_ms)
            )
            recording_thread.start()
            
            logger.info(f"Started recording automation for plugin {plugin_id}")
            
            # Wait a moment then return initial status
            time.sleep(0.1)
            
            return {
                'success': True,
                'recording_id': recording_id,
                'plugin_id': plugin_id,
                'parameters': parameters,
                'duration_ms': duration_ms,
                'status': 'recording',
                'event_count': 0
            }
            
        except Exception as e:
            logger.error(f"Failed to record automation: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _record_parameters(self, recording_id: str, plugin_id: int,
                          parameters: List[int], duration_ms: int):
        """Record parameter values in thread
        
        Args:
            recording_id: Recording session ID
            plugin_id: Plugin ID
            parameters: List of parameter indices
            duration_ms: Recording duration
        """
        try:
            recording = self.recording_sessions[recording_id]
            start_time = time.time()
            sample_interval = 0.01  # 10ms sampling
            
            while (time.time() - start_time) * 1000 < duration_ms:
                timestamp = time.time() - start_time
                
                for param_id in parameters:
                    value = self.carla.get_parameter(plugin_id, param_id)
                    recording['parameters'][param_id]['values'].append(value)
                    recording['parameters'][param_id]['timestamps'].append(timestamp)
                
                time.sleep(sample_interval)
            
            # Mark recording as complete
            recording['status'] = 'complete'
            recording['event_count'] = len(recording['parameters'][parameters[0]]['values'])
            
            logger.info(f"Completed recording {recording_id} with {recording['event_count']} events")
            
        except Exception as e:
            logger.error(f"Recording error: {str(e)}")
            if recording_id in self.recording_sessions:
                self.recording_sessions[recording_id]['status'] = 'error'
                self.recording_sessions[recording_id]['error'] = str(e)
    
    async def set_parameter(self, plugin_id: str, parameter_id: int, value: float,
                          session_context: dict = None, **kwargs) -> dict:
        """Set a parameter value
        
        Args:
            plugin_id: Plugin ID
            parameter_id: Parameter index
            value: Parameter value (0.0 to 1.0)
            
        Returns:
            Parameter update result
        """
        try:
            plugin_id = validate_plugin_id(plugin_id, self.carla)

            # Get parameter info
            param_info = self.carla.get_parameter_info(plugin_id, parameter_id)

            # Set the parameter
            self.carla.set_parameter(plugin_id, parameter_id, value)
            
            # Get display text
            text = self.carla.host.get_parameter_text(plugin_id, parameter_id)
            
            logger.info(f"Set plugin {plugin_id} param {parameter_id} to {value}")
            
            return {
                'success': True,
                'plugin_id': plugin_id,
                'parameter_id': parameter_id,
                'parameter_name': param_info['name'],
                'value': value,
                'display_text': text,
                'unit': param_info['unit']
            }
            
        except Exception as e:
            logger.error(f"Failed to set parameter: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def get_parameter(self, plugin_id: str, parameter_id: int,
                          session_context: dict = None, **kwargs) -> dict:
        """Get a parameter value
        
        Args:
            plugin_id: Plugin ID
            parameter_id: Parameter index
            
        Returns:
            Parameter value and info
        """
        try:
            plugin_id = validate_plugin_id(plugin_id, self.carla)

            # Get parameter info and value
            param_info = self.carla.get_parameter_info(plugin_id, parameter_id)
            value = self.carla.get_parameter(plugin_id, parameter_id)
            text = self.carla.host.get_parameter_text(plugin_id, parameter_id)
            
            return {
                'success': True,
                'plugin_id': plugin_id,
                'parameter_id': parameter_id,
                'parameter_name': param_info['name'],
                'value': value,
                'display_text': text,
                'unit': param_info['unit'],
                'min': param_info['min'],
                'max': param_info['max'],
                'default': param_info['default']
            }
            
        except Exception as e:
            logger.error(f"Failed to get parameter: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def randomize_parameters(self, plugin_id: str, amount: float = 0.5,
                                  exclude: Optional[List[int]] = None,
                                  session_context: dict = None, **kwargs) -> dict:
        """Randomize plugin parameters
        
        Args:
            plugin_id: Plugin ID
            amount: Randomization amount (0.0 to 1.0)
            exclude: List of parameter indices to exclude
            
        Returns:
            Randomization result
        """
        try:
            plugin_id = validate_plugin_id(plugin_id, self.carla)

            exclude = exclude or []
            param_count = self.carla.host.get_parameter_count(plugin_id)
            randomized = []
            
            for param_id in range(param_count):
                if param_id in exclude:
                    continue
                
                # Get parameter info
                param_info = self.carla.get_parameter_info(plugin_id, param_id)
                
                # Skip non-automatable parameters
                if param_info.get('hints', 0) & 0x100:  # IS_AUTOMATABLE flag
                    continue
                
                # Calculate random value
                current = param_info['current']
                min_val = param_info['min']
                max_val = param_info['max']
                
                # Random within range, weighted by amount
                random_val = random.uniform(min_val, max_val)
                new_val = current + (random_val - current) * amount
                
                # Set the parameter
                self.carla.set_parameter(plugin_id, param_id, new_val)
                
                randomized.append({
                    'param_id': param_id,
                    'name': param_info['name'],
                    'old_value': current,
                    'new_value': new_val
                })
            
            logger.info(f"Randomized {len(randomized)} parameters for plugin {plugin_id}")
            
            return {
                'success': True,
                'plugin_id': plugin_id,
                'randomized_count': len(randomized),
                'amount': amount,
                'parameters': randomized[:10]  # Return first 10 for preview
            }
            
        except Exception as e:
            logger.error(f"Failed to randomize parameters: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def morph_parameters(self, plugin_id: str, target_state: dict,
                             duration_ms: int = 1000, session_context: dict = None, **kwargs) -> dict:
        """Morph parameters to target state
        
        Args:
            plugin_id: Plugin ID
            target_state: Target parameter values
            duration_ms: Morphing duration
            
        Returns:
            Morphing result
        """
        try:
            plugin_id = validate_plugin_id(plugin_id, self.carla)

            # Get current state
            current_state = {}
            for param_id, target_value in target_state.items():
                param_id = int(param_id)
                current_state[param_id] = self.carla.get_parameter(plugin_id, param_id)
            
            # Start morphing thread
            morph_thread = threading.Thread(
                target=self._morph_parameters,
                args=(plugin_id, current_state, target_state, duration_ms)
            )
            morph_thread.start()
            
            logger.info(f"Started parameter morphing for plugin {plugin_id}")
            
            return {
                'success': True,
                'plugin_id': plugin_id,
                'parameter_count': len(target_state),
                'duration_ms': duration_ms,
                'morphing': True
            }
            
        except Exception as e:
            logger.error(f"Failed to morph parameters: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _morph_parameters(self, plugin_id: int, current_state: dict,
                         target_state: dict, duration_ms: int):
        """Morph parameters in thread
        
        Args:
            plugin_id: Plugin ID
            current_state: Current parameter values
            target_state: Target parameter values
            duration_ms: Morphing duration
        """
        try:
            steps = int(duration_ms / 10)
            
            for i in range(steps):
                progress = i / steps
                
                for param_id in current_state:
                    current = current_state[param_id]
                    target = target_state[str(param_id)]
                    
                    # Linear interpolation
                    value = current + (target - current) * progress
                    self.carla.set_parameter(plugin_id, param_id, value)
                
                time.sleep(0.01)
            
            # Set final values
            for param_id, target in target_state.items():
                self.carla.set_parameter(plugin_id, int(param_id), target)
            
            logger.info(f"Completed parameter morphing for plugin {plugin_id}")
            
        except Exception as e:
            logger.error(f"Morphing error: {str(e)}")