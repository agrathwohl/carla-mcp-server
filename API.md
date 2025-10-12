# Carla MCP Server API Reference

Complete reference for all available MCP tools in the Carla MCP Server.

## Overview

The Carla MCP Server provides 45+ tools across 7 functional categories for complete control over the Carla audio plugin host. All tools are asynchronous and return JSON responses.

## Table of Contents

- [Session Management](#session-management)
- [Plugin Control](#plugin-control)
- [Audio Routing](#audio-routing)
- [Parameter Automation](#parameter-automation)
- [Real-Time Analysis](#real-time-analysis)
- [JACK Integration](#jack-integration)
- [Hardware Control](#hardware-control)

---

## Session Management

### load_session

Load a Carla project/session file.

**Parameters:**
- `path` (string, required): Path to .carxp project file
- `auto_connect` (boolean, optional): Auto-connect JACK ports (default: true)

**Returns:**
```json
{
  "success": true,
  "session_id": "session_123",
  "plugin_count": 5,
  "message": "Session loaded successfully"
}
```

**Example:**
```python
result = await client.call_tool("load_session", {
    "path": "/path/to/project.carxp",
    "auto_connect": True
})
```

### save_session

Save current session to file.

**Parameters:**
- `path` (string, required): Save location
- `include_samples` (boolean, optional): Include audio samples (default: true)
- `compress` (boolean, optional): Compress project file (default: false)

**Returns:**
```json
{
  "success": true,
  "saved_path": "/path/to/project.carxp",
  "file_size_mb": 15.2
}
```

### create_snapshot

Create session snapshot for A/B comparison.

**Parameters:**
- `name` (string, required): Snapshot name
- `include_audio_files` (boolean, optional): Include audio files (default: false)

**Returns:**
```json
{
  "success": true,
  "snapshot_id": "snap_456",
  "name": "Before EQ",
  "created_at": "2025-01-15T10:30:00Z"
}
```

### switch_session

Hot-swap between sessions with optional crossfade.

**Parameters:**
- `session_id` (string, required): Target session ID
- `crossfade_ms` (integer, optional): Crossfade duration in milliseconds (default: 0)

**Returns:**
```json
{
  "success": true,
  "previous_session": "session_123",
  "current_session": "session_456",
  "crossfade_applied": true
}
```

### list_sessions

List all available sessions.

**Returns:**
```json
{
  "success": true,
  "sessions": [
    {
      "id": "session_123",
      "name": "My Project",
      "path": "/path/to/project.carxp",
      "plugin_count": 5,
      "created_at": "2025-01-15T09:00:00Z"
    }
  ]
}
```

### delete_session

Delete a session.

**Parameters:**
- `session_id` (string, required): Session ID to delete

**Returns:**
```json
{
  "success": true,
  "message": "Session deleted successfully"
}
```

### export_session

Export session to different format.

**Parameters:**
- `session_id` (string, required): Session to export
- `export_path` (string, required): Export destination
- `format` (string, optional): Export format (default: "wav")

**Returns:**
```json
{
  "success": true,
  "export_path": "/path/to/export.wav",
  "duration_seconds": 180.5
}
```

### import_session

Import session from external format.

**Parameters:**
- `import_path` (string, required): Path to import file
- `format` (string, optional): Source format (default: "auto")

**Returns:**
```json
{
  "success": true,
  "session_id": "session_789",
  "imported_tracks": 8
}
```

---

## Plugin Control

### load_plugin

Load any plugin format (VST2/3, LV2, etc.).

**Parameters:**
- `path` (string, required): Plugin path or URI
- `type` (string, required): Plugin type - "VST2", "VST3", "LV2", "LADSPA", "AU"
- `position` (integer, optional): Rack position (default: -1 for end)
- `preset` (string, optional): Optional preset to load

**Returns:**
```json
{
  "success": true,
  "plugin_id": "plugin_1",
  "name": "FabFilter Pro-Q 3",
  "type": "VST3",
  "audio_ins": 2,
  "audio_outs": 2,
  "parameters": 24
}
```

### scan_plugins

Scan directory for available plugins.

**Parameters:**
- `directory` (string, required): Directory to scan
- `formats` (array, optional): Plugin formats to scan
- `recursive` (boolean, optional): Scan subdirectories (default: true)

**Returns:**
```json
{
  "success": true,
  "plugins_found": [
    {
      "path": "/usr/lib/vst3/plugin.vst3",
      "name": "Reverb",
      "type": "VST3",
      "manufacturer": "Company"
    }
  ],
  "scan_time_ms": 1250
}
```

### control_plugin

Control plugin state (activate, bypass, solo, remove).

**Parameters:**
- `plugin_id` (string, required): Plugin ID
- `action` (string, required): "activate", "bypass", "solo", "remove"
- `fade_ms` (integer, optional): Fade time in milliseconds (default: 0)

**Returns:**
```json
{
  "success": true,
  "plugin_id": "plugin_1",
  "action": "bypass",
  "new_state": "bypassed"
}
```

### batch_process

Apply plugin chain to audio files.

**Parameters:**
- `input_file` (string, required): Input audio file path
- `plugin_chain` (array, required): List of plugin IDs to apply
- `output_file` (string, required): Output file path

**Returns:**
```json
{
  "success": true,
  "input_file": "/input.wav",
  "output_file": "/output.wav",
  "processing_time_ms": 2340,
  "plugins_applied": 3
}
```

### list_plugins

List all loaded plugins.

**Returns:**
```json
{
  "success": true,
  "plugins": [
    {
      "id": "plugin_1",
      "name": "EQ",
      "type": "VST3",
      "active": true,
      "position": 0
    }
  ]
}
```

### get_plugin_info

Get detailed information about a plugin.

**Parameters:**
- `plugin_id` (string, required): Plugin ID

**Returns:**
```json
{
  "success": true,
  "plugin": {
    "id": "plugin_1",
    "name": "FabFilter Pro-Q 3",
    "type": "VST3",
    "parameters": [
      {
        "id": 0,
        "name": "Frequency",
        "value": 440.0,
        "min": 20.0,
        "max": 20000.0
      }
    ]
  }
}
```

### clone_plugin

Create a copy of an existing plugin.

**Parameters:**
- `plugin_id` (string, required): Plugin ID to clone

**Returns:**
```json
{
  "success": true,
  "original_plugin": "plugin_1",
  "cloned_plugin": "plugin_2",
  "parameters_copied": 24
}
```

### replace_plugin

Replace one plugin with another.

**Parameters:**
- `plugin_id` (string, required): Plugin ID to replace
- `new_path` (string, required): New plugin path
- `new_type` (string, required): New plugin type

**Returns:**
```json
{
  "success": true,
  "old_plugin": "plugin_1",
  "new_plugin": "plugin_1",
  "parameters_mapped": 18
}
```

---

## Audio Routing

### connect_audio

Create audio connections between plugins.

**Parameters:**
- `source` (object, required):
  - `plugin_id` (string): Source plugin ID
  - `port_index` (integer): Source port index
- `destination` (object, required):
  - `plugin_id` (string): Destination plugin ID
  - `port_index` (integer): Destination port index
- `gain` (number, optional): Connection gain in dB (default: 0)

**Returns:**
```json
{
  "success": true,
  "connection_id": "conn_123",
  "source": "plugin_1:0",
  "destination": "plugin_2:0",
  "gain_db": 0.0
}
```

### create_bus

Create audio bus for grouping.

**Parameters:**
- `name` (string, required): Bus name
- `channels` (integer, optional): Number of channels (default: 2)
- `plugins` (array, optional): Plugin IDs to assign to bus

**Returns:**
```json
{
  "success": true,
  "bus_id": "bus_drums",
  "name": "Drums",
  "channels": 2,
  "plugins_assigned": 3
}
```

### setup_sidechain

Configure sidechain routing.

**Parameters:**
- `source_plugin` (string, required): Source plugin ID
- `destination_plugin` (string, required): Destination plugin ID
- `sidechain_input` (integer, optional): Sidechain input index (default: 0)

**Returns:**
```json
{
  "success": true,
  "sidechain_id": "sc_123",
  "source": "plugin_kick",
  "destination": "plugin_bass_comp"
}
```

### get_routing_matrix

Get complete routing configuration.

**Parameters:**
- `format` (string, optional): "json", "graphviz", "matrix" (default: "json")

**Returns:**
```json
{
  "success": true,
  "format": "json",
  "connections": [
    {
      "id": "conn_123",
      "source": "plugin_1:0",
      "destination": "plugin_2:0"
    }
  ],
  "buses": ["bus_drums", "bus_vocals"]
}
```

### disconnect_audio

Disconnect audio connection.

**Parameters:**
- `connection_id` (string, required): Connection ID to disconnect

**Returns:**
```json
{
  "success": true,
  "connection_id": "conn_123",
  "message": "Connection removed"
}
```

### create_send

Create send/return effect routing.

**Parameters:**
- `source_plugin` (string, required): Source plugin ID
- `send_plugin` (string, required): Send effect plugin ID
- `amount` (number, optional): Send amount 0.0-1.0 (default: 0.5)

**Returns:**
```json
{
  "success": true,
  "send_id": "send_123",
  "source": "plugin_vocals",
  "effect": "plugin_reverb",
  "amount": 0.5
}
```

### set_connection_gain

Adjust connection gain.

**Parameters:**
- `connection_id` (string, required): Connection ID
- `gain` (number, required): Gain in dB

**Returns:**
```json
{
  "success": true,
  "connection_id": "conn_123",
  "new_gain_db": -6.0
}
```

---

## Parameter Automation

### automate_parameter

Create parameter automation.

**Parameters:**
- `plugin_id` (string, required): Plugin ID
- `parameter_id` (integer, required): Parameter index
- `automation_type` (string, required): "linear", "exponential", "sine", "random_walk"
- `duration_ms` (integer, required): Duration in milliseconds
- `values` (array, optional): Keyframe values

**Returns:**
```json
{
  "success": true,
  "automation_id": "auto_123",
  "plugin_id": "plugin_1",
  "parameter_id": 0,
  "keyframes": 10
}
```

### map_midi_cc

Map MIDI CC to parameters.

**Parameters:**
- `plugin_id` (string, required): Plugin ID
- `parameter_id` (integer, required): Parameter index
- `cc_number` (integer, required): MIDI CC number (0-127)
- `channel` (integer, optional): MIDI channel 1-16 (default: 1)
- `range` (object, optional): Min/max range
- `curve` (string, optional): "linear", "exponential", "logarithmic" (default: "linear")

**Returns:**
```json
{
  "success": true,
  "mapping_id": "midi_123",
  "plugin_id": "plugin_1",
  "parameter_id": 0,
  "cc_number": 74
}
```

### create_macro

Control multiple parameters with one macro.

**Parameters:**
- `name` (string, required): Macro name
- `targets` (array, required): Target parameters with ranges and curves

**Returns:**
```json
{
  "success": true,
  "macro_id": "macro_filter",
  "name": "Filter Sweep",
  "targets": 2
}
```

### record_automation

Record parameter changes.

**Parameters:**
- `plugin_id` (string, required): Plugin ID
- `parameters` (array, required): Parameter indices to record
- `duration_ms` (integer, required): Recording duration

**Returns:**
```json
{
  "success": true,
  "recording_id": "rec_123",
  "plugin_id": "plugin_1",
  "parameters_recorded": 3,
  "events_captured": 156
}
```

### set_parameter

Set parameter value directly.

**Parameters:**
- `plugin_id` (string, required): Plugin ID
- `parameter_id` (integer, required): Parameter index
- `value` (number, required): Parameter value

**Returns:**
```json
{
  "success": true,
  "plugin_id": "plugin_1",
  "parameter_id": 0,
  "old_value": 440.0,
  "new_value": 880.0
}
```

### get_parameter

Get current parameter value.

**Parameters:**
- `plugin_id` (string, required): Plugin ID
- `parameter_id` (integer, required): Parameter index

**Returns:**
```json
{
  "success": true,
  "plugin_id": "plugin_1",
  "parameter_id": 0,
  "name": "Frequency",
  "value": 440.0,
  "min": 20.0,
  "max": 20000.0
}
```

### randomize_parameters

Randomize parameter values.

**Parameters:**
- `plugin_id` (string, required): Plugin ID
- `amount` (number, optional): Randomization amount 0.0-1.0 (default: 0.5)
- `parameters` (array, optional): Specific parameters to randomize

**Returns:**
```json
{
  "success": true,
  "plugin_id": "plugin_1",
  "parameters_randomized": 12,
  "amount": 0.5
}
```

### morph_parameters

Morph between parameter states.

**Parameters:**
- `plugin_id` (string, required): Plugin ID
- `target_state` (object, required): Target parameter values
- `duration_ms` (integer, required): Morph duration

**Returns:**
```json
{
  "success": true,
  "morph_id": "morph_123",
  "plugin_id": "plugin_1",
  "parameters_morphed": 8,
  "duration_ms": 2000
}
```

---

## Real-Time Analysis

### analyze_spectrum

Real-time spectrum analysis.

**Parameters:**
- `source` (string, required): Plugin ID or bus ID
- `fft_size` (integer, optional): FFT size 512-8192 (default: 2048)
- `window` (string, optional): "hann", "blackman", "hamming" (default: "hann")

**Returns:**
```json
{
  "success": true,
  "source": "plugin_1",
  "spectrum": {
    "frequencies": [20, 25, 31.5, "..."],
    "magnitudes": [-60.2, -58.1, -55.7, "..."],
    "peak_frequency": 440.0,
    "peak_magnitude": -12.3
  }
}
```

### measure_levels

Get audio levels and statistics.

**Parameters:**
- `source` (string, required): Plugin ID or bus ID
- `window_ms` (integer, optional): Measurement window (default: 100)
- `include_history` (boolean, optional): Include level history (default: false)

**Returns:**
```json
{
  "success": true,
  "source": "plugin_1",
  "levels": {
    "peak_db": [-12.3, -14.1],
    "rms_db": [-18.7, -20.2],
    "lufs": -16.2,
    "true_peak_db": -11.8
  }
}
```

### capture_plugin_parameters

Capture all parameter values over time.

**Parameters:**
- `plugin_ids` (string|array, required): Plugin ID(s)
- `capture_duration_ms` (integer, optional): Duration (default: 10000)
- `sampling_interval_ms` (integer, optional): Sample interval (default: 100)

**Returns:**
```json
{
  "success": true,
  "capture_id": "cap_123",
  "plugins": ["plugin_1"],
  "duration_ms": 10000,
  "samples_captured": 100
}
```

### detect_feedback

Detect feedback loops in routing.

**Parameters:**
- `sensitivity` (number, optional): Detection sensitivity 0.0-1.0 (default: 0.8)
- `threshold_db` (number, optional): Feedback threshold (default: -20.0)

**Returns:**
```json
{
  "success": true,
  "feedback_detected": true,
  "feedback_loops": [
    {
      "source": "plugin_1",
      "destination": "plugin_2",
      "gain_db": -15.2
    }
  ]
}
```

### analyze_latency

Measure system and plugin latencies.

**Parameters:**
- `measure_plugins` (boolean, optional): Include plugin latencies (default: true)
- `test_duration_ms` (integer, optional): Test duration (default: 1000)

**Returns:**
```json
{
  "success": true,
  "system_latency_ms": 5.3,
  "plugin_latencies": {
    "plugin_1": 2.1,
    "plugin_2": 0.8
  },
  "total_latency_ms": 8.2
}
```

---

## JACK Integration

### list_jack_ports

List available JACK ports.

**Parameters:**
- `port_type` (string, optional): Filter by type ("audio", "midi")
- `flags` (string, optional): Filter by flags ("input", "output", "physical")
- `name_pattern` (string, optional): Filter by name pattern

**Returns:**
```json
{
  "success": true,
  "ports": [
    {
      "name": "system:capture_1",
      "type": "audio",
      "flags": ["input", "physical"],
      "connected": true
    }
  ]
}
```

### connect_jack_ports

Connect two JACK ports.

**Parameters:**
- `source` (string, required): Source port name
- `destination` (string, required): Destination port name

**Returns:**
```json
{
  "success": true,
  "connection": "system:capture_1 -> carla:audio-in1",
  "message": "Ports connected successfully"
}
```

### disconnect_jack_ports

Disconnect two JACK ports.

**Parameters:**
- `source` (string, required): Source port name
- `destination` (string, required): Destination port name

**Returns:**
```json
{
  "success": true,
  "connection": "system:capture_1 -> carla:audio-in1",
  "message": "Ports disconnected successfully"
}
```

### get_jack_connections

Get connections for a specific port.

**Parameters:**
- `port` (string, optional): Port name (if not provided, returns all connections)

**Returns:**
```json
{
  "success": true,
  "connections": [
    {
      "source": "system:capture_1",
      "destination": "carla:audio-in1"
    }
  ]
}
```

### connect_system_to_plugin

Connect system audio inputs to plugin.

**Parameters:**
- `plugin_id` (integer, required): Plugin ID
- `system_channels` (array, optional): System channel indices

**Returns:**
```json
{
  "success": true,
  "plugin_id": 1,
  "connections_made": 2,
  "channels": ["system:capture_1", "system:capture_2"]
}
```

### connect_plugin_to_system

Connect plugin outputs to system.

**Parameters:**
- `plugin_id` (integer, required): Plugin ID
- `system_channels` (array, optional): System channel indices

**Returns:**
```json
{
  "success": true,
  "plugin_id": 1,
  "connections_made": 2,
  "channels": ["system:playback_1", "system:playback_2"]
}
```

---

## Hardware Control

### configure_audio_interface

Configure audio hardware settings.

**Parameters:**
- `device` (string, required): Device name
- `sample_rate` (integer, optional): Sample rate in Hz
- `buffer_size` (integer, optional): Buffer size in samples
- `channels_in` (integer, optional): Input channel count
- `channels_out` (integer, optional): Output channel count

**Returns:**
```json
{
  "success": true,
  "device": "Focusrite Scarlett 2i2",
  "sample_rate": 48000,
  "buffer_size": 256,
  "latency_ms": 5.3
}
```

### list_audio_devices

List available audio devices.

**Parameters:**
- `driver` (string, optional): Audio driver ("JACK", "ALSA", etc.)

**Returns:**
```json
{
  "success": true,
  "devices": [
    {
      "name": "Focusrite Scarlett 2i2",
      "driver": "JACK",
      "channels_in": 2,
      "channels_out": 2,
      "sample_rates": [44100, 48000, 96000]
    }
  ]
}
```

### map_control_surface

Map hardware controller to parameters.

**Parameters:**
- `device_name` (string, required): Controller device name
- `mapping_preset` (string, optional): Predefined mapping preset

**Returns:**
```json
{
  "success": true,
  "device": "Akai APC40",
  "mapping_preset": "live_performance",
  "controls_mapped": 16
}
```

---

## Error Handling

All tools return a consistent error format when operations fail:

```json
{
  "success": false,
  "error": "PluginNotFound",
  "message": "Plugin with ID 'plugin_1' not found",
  "details": {
    "plugin_id": "plugin_1",
    "available_plugins": ["plugin_2", "plugin_3"]
  }
}
```

## Common Error Types

- `PluginNotFound`: Plugin ID not found
- `SessionNotFound`: Session ID not found
- `InvalidParameter`: Invalid parameter value or type
- `AudioEngineError`: Carla engine error
- `JackConnectionError`: JACK-related errors
- `FileNotFound`: File path not found
- `PermissionDenied`: Insufficient permissions
- `InvalidConfiguration`: Configuration error

## Rate Limiting

Some tools have built-in rate limiting to prevent audio dropouts:

- Real-time analysis tools: 10 requests/second
- Parameter automation: 100 events/second
- JACK operations: 20 requests/second

---

## Natural Language Usage Examples

The Carla MCP Server is designed to work with Large Language Models, allowing natural language control of audio production workflows. Here are examples of prompts you can give to an LLM and the actions it would take:

### Example 1: Basic Session Setup

**User Prompt:** *"Load my mixing project and set up a drum bus with compression"*

**LLM Response & Actions:**
```
I'll help you load your project and set up a drum bus with compression. Let me:

1. First, load your session
2. Create a drum bus
3. Load a compressor plugin
4. Route your drum tracks to the bus

Setting up your mixing session now...
```

**Tools Called:**
1. `load_session` - Load the project file
2. `create_bus` - Create "Drums" bus with 2 channels
3. `load_plugin` - Load compressor plugin (e.g., "VST3:/path/to/compressor.vst3")
4. `connect_audio` - Route drum tracks to the new bus

### Example 2: Creative Sound Design

**User Prompt:** *"Create an interesting filter sweep effect on the lead synth, sync it to the tempo"*

**LLM Response & Actions:**
```
I'll create a dynamic filter sweep effect for your lead synth! This will involve:

1. Finding your lead synth plugin
2. Setting up parameter automation for the filter
3. Creating a tempo-synced sine wave modulation

Creating the filter sweep automation...
```

**Tools Called:**
1. `list_plugins` - Find the lead synth plugin
2. `get_plugin_info` - Get parameter information
3. `automate_parameter` - Create filter frequency automation with sine wave
4. `map_midi_cc` - Optionally map to MIDI controller for real-time control

### Example 3: Problem Solving

**User Prompt:** *"My mix sounds muddy and I'm getting some feedback. Can you help identify and fix these issues?"*

**LLM Response & Actions:**
```
I'll help diagnose and fix the muddiness and feedback issues. Let me:

1. Analyze the frequency spectrum to identify muddy frequencies
2. Check for feedback loops in your routing
3. Suggest and apply corrective EQ
4. Fix any problematic connections

Running audio analysis...
```

**Tools Called:**
1. `analyze_spectrum` - Analyze frequency content across all tracks
2. `detect_feedback` - Scan for feedback loops in routing
3. `load_plugin` - Load EQ plugins where needed
4. `automate_parameter` - Apply high-pass filters to remove muddiness
5. `disconnect_audio` - Break feedback loops if found

### Example 4: Advanced Production Workflow

**User Prompt:** *"Set up a parallel compression chain for my vocals with different settings for verse and chorus sections"*

**LLM Response & Actions:**
```
I'll set up a sophisticated parallel compression system for your vocals with section-specific automation:

1. Create a parallel routing setup
2. Load multiple compressors with different settings
3. Set up automation to switch between verse and chorus compression
4. Create macro controls for easy adjustment

Building your vocal processing chain...
```

**Tools Called:**
1. `get_plugin_info` - Identify vocal track
2. `create_send` - Create parallel send to compression bus
3. `load_plugin` - Load 2 different compressor instances
4. `automate_parameter` - Automate compressor settings for verse/chorus
5. `create_macro` - Create "Vocal Intensity" macro control
6. `set_connection_gain` - Balance parallel blend

### Example 5: Live Performance Setup

**User Prompt:** *"Prepare this session for live performance - I need low latency and MIDI controller mapping for the main effects"*

**LLM Response & Actions:**
```
I'll optimize your session for live performance! This involves:

1. Checking and optimizing audio latency
2. Setting up MIDI controller mappings for key parameters
3. Creating performance macros for quick control
4. Ensuring stable audio routing

Configuring for live performance...
```

**Tools Called:**
1. `analyze_latency` - Measure current system latency
2. `configure_audio_interface` - Optimize buffer size for low latency
3. `map_midi_cc` - Map controller knobs to key effect parameters
4. `create_macro` - Create performance macros (e.g., "Build Up", "Drop")
5. `list_jack_ports` - Verify audio routing stability

### Example 6: Batch Processing

**User Prompt:** *"Apply the same reverb and EQ chain to all my recorded guitar takes, then export them as individual files"*

**LLM Response & Actions:**
```
I'll process all your guitar takes with consistent reverb and EQ, then export them individually:

1. Identify all guitar tracks
2. Create a standardized processing chain
3. Apply the same settings to each take
4. Export each processed take as a separate file

Processing your guitar takes...
```

**Tools Called:**
1. `list_plugins` - Find all guitar track plugins
2. `clone_plugin` - Clone EQ and reverb settings to each track
3. `batch_process` - Apply processing chain to each audio file
4. `export_session` - Export individual processed takes

### Example 7: Mix Analysis and Improvement

**User Prompt:** *"Analyze my mix and suggest improvements for better stereo imaging and frequency balance"*

**LLM Response & Actions:**
```
Let me analyze your mix comprehensively and provide targeted improvements:

1. Analyzing frequency spectrum across all tracks
2. Measuring stereo width and imaging
3. Checking for phase issues and masking
4. Implementing suggested improvements

Running mix analysis...
```

**Tools Called:**
1. `analyze_spectrum` - Analyze frequency content of full mix
2. `measure_levels` - Check dynamic range and peak levels
3. `capture_plugin_parameters` - Capture current EQ/dynamics settings
4. `automate_parameter` - Apply corrective EQ moves
5. `load_plugin` - Add stereo imaging plugins where beneficial

### Example 8: Session Comparison and A/B Testing

**User Prompt:** *"I want to compare two different master bus processing chains - help me A/B test them quickly"*

**LLM Response & Actions:**
```
I'll set up an A/B comparison system for your master bus processing:

1. Create snapshots of your current state
2. Set up quick switching between processing chains
3. Implement instant recall for easy comparison
4. Add visual feedback for which version is active

Setting up A/B comparison system...
```

**Tools Called:**
1. `create_snapshot` - Snapshot current master bus state ("Version A")
2. `clone_plugin` - Create alternative processing chain
3. `replace_plugin` - Swap master bus plugins for "Version B"
4. `create_snapshot` - Snapshot alternative state ("Version B")
5. `switch_session` - Quick switching between snapshots

### LLM Interaction Patterns

The LLM can understand and respond to various types of requests:

**Technical Requests:** "Route the sidechain from kick to bass compressor"
**Creative Requests:** "Make the breakdown section more intense"
**Problem-Solving:** "Why is there latency in my monitoring?"
**Workflow:** "Set up a template for recording vocals"
**Analysis:** "What's causing the harsh frequencies in my mix?"
**Performance:** "Prepare this for live playback"

### Contextual Understanding

The LLM maintains context throughout conversations:

```
User: "Load my rock song project"
LLM: [Loads session] "Loaded your rock project with 12 tracks"

User: "Add some warmth to the vocals"
LLM: [Remembers vocal track from previous action] "Adding tape saturation and warm EQ to the lead vocal track"

User: "Now do the same for the backing vocals"
LLM: [Applies similar processing to backing vocals] "Applied similar warmth processing to tracks 3 and 4"
```

This natural language interface makes professional audio production accessible through conversational AI, allowing both beginners and experts to work more efficiently with complex audio routing and processing tasks.

---

*This documentation covers all 45+ tools available in the Carla MCP Server. For usage examples and tutorials, see the main README.md file.*