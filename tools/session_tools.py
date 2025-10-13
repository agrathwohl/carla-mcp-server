#!/usr/bin/env python3
"""
Session Management Tools for Carla MCP Server
"""

import os
import json
import shutil
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
import uuid

logger = logging.getLogger(__name__)


class SessionTools:
    """Session management tools for Carla"""
    
    def __init__(self, carla_controller):
        """Initialize session tools
        
        Args:
            carla_controller: CarlaController instance
        """
        self.carla = carla_controller
        self.sessions = {}
        self.snapshots = {}
        self.active_session = None
        
        # Create session storage directory
        self.session_dir = Path.home() / ".carla-mcp" / "sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info("SessionTools initialized")
    
    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Execute a session tool
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            
        Returns:
            Tool execution result
        """
        if tool_name == "load_session":
            return await self.load_session(**arguments)
        elif tool_name == "save_session":
            return await self.save_session(**arguments)
        elif tool_name == "create_snapshot":
            return await self.create_snapshot(**arguments)
        elif tool_name == "switch_session":
            return await self.switch_session(**arguments)
        elif tool_name == "list_sessions":
            return await self.list_sessions(**arguments)
        elif tool_name == "delete_session":
            return await self.delete_session(**arguments)
        elif tool_name == "export_session":
            return await self.export_session(**arguments)
        elif tool_name == "import_session":
            return await self.import_session(**arguments)
        else:
            raise ValueError(f"Unknown session tool: {tool_name}")
    
    async def load_session(self, path: str, auto_connect: bool = True, 
                          session_context: dict = None, **kwargs) -> dict:
        """Load a Carla session
        
        Args:
            path: Path to session file
            auto_connect: Auto-connect JACK ports
            
        Returns:
            Session information
        """
        warnings = []
        
        try:
            # Ensure engine is running
            if not self.carla.engine_running:
                self.carla.start_engine()
            
            # Load the project - this is the critical operation
            success = self.carla.load_project(path)
            
            if not success:
                raise Exception(f"Failed to load project: {path}")
            
            # Generate session ID
            session_id = str(uuid.uuid4())
            
            # Get plugin information - non-critical, collect warnings
            plugin_count = self.carla.host.get_current_plugin_count()
            plugins = []
            
            for i in range(plugin_count):
                try:
                    info = self.carla.host.get_plugin_info(i)
                    if info:
                        plugins.append({
                            'id': i,
                            'name': info.get('name', f'Plugin_{i}'),
                            'type': info.get('label', 'Unknown'),
                            'audio_ins': info.get('audioIns', 0),  # Safe access
                            'audio_outs': info.get('audioOuts', 0),  # Safe access
                            'parameters': self.carla.host.get_parameter_count(i)
                        })
                    else:
                        # Plugin exists but info unavailable
                        plugins.append({
                            'id': i,
                            'name': f'Plugin_{i}',
                            'type': 'Unknown',
                            'audio_ins': 0,
                            'audio_outs': 0,
                            'parameters': self.carla.host.get_parameter_count(i)
                        })
                        warnings.append(f"Plugin {i}: info unavailable, using defaults")
                except Exception as e:
                    warnings.append(f"Plugin {i}: failed to get info - {str(e)}")
                    # Add minimal plugin info so we don't lose track
                    plugins.append({
                        'id': i,
                        'name': f'Plugin_{i}',
                        'type': 'Unknown',
                        'audio_ins': 0,
                        'audio_outs': 0,
                        'parameters': 0
                    })
            
            # Auto-connect if requested
            if auto_connect:
                try:
                    self.carla.refresh_connections()
                    # Patchbay connections are loaded from the project file automatically
                except Exception as e:
                    warnings.append(f"Auto-connect failed: {str(e)}")
            
            # Store session info
            self.sessions[session_id] = {
                'id': session_id,
                'path': path,
                'name': Path(path).stem,
                'loaded_at': datetime.now().isoformat(),
                'plugin_count': plugin_count,
                'plugins': plugins,
                'auto_connected': auto_connect
            }
            
            self.active_session = session_id
            
            logger.info(f"Loaded session {session_id}: {path}")
            if warnings:
                logger.warning(f"Session loaded with warnings: {warnings}")
            
            result = {
                'success': True,
                'session_id': session_id,
                'name': self.sessions[session_id]['name'],
                'plugin_count': plugin_count,
                'plugins': plugins,
                'sample_rate': self.carla.host.get_sample_rate(),
                'buffer_size': self.carla.host.get_buffer_size()
            }
            
            # Add warnings if any occurred
            if warnings:
                result['warnings'] = warnings
                
            return result
            
        except Exception as e:
            logger.error(f"Failed to load session: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def save_session(self, path: str, include_samples: bool = True,
                          compress: bool = False, session_context: dict = None, **kwargs) -> dict:
        """Save current session
        
        Args:
            path: Save location
            include_samples: Include audio samples
            compress: Compress the session
            
        Returns:
            Save result
        """
        try:
            # Ensure we have an active session
            if not self.active_session:
                # Create a new session entry
                session_id = str(uuid.uuid4())
                self.sessions[session_id] = {
                    'id': session_id,
                    'name': Path(path).stem,
                    'created_at': datetime.now().isoformat()
                }
                self.active_session = session_id
            
            # Save the project
            success = self.carla.save_project(path)
            
            if not success:
                raise Exception(f"Failed to save project to: {path}")
            
            # Update session info
            self.sessions[self.active_session]['path'] = path
            self.sessions[self.active_session]['saved_at'] = datetime.now().isoformat()
            
            # Get file size
            file_size = os.path.getsize(path)
            
            # Create backup
            backup_path = path + ".backup"
            shutil.copy2(path, backup_path)
            
            # Compress if requested
            if compress:
                import zipfile
                zip_path = path + ".zip"
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    zipf.write(path, Path(path).name)
                    if include_samples:
                        # Note: Carla's save_project already embeds samples when compression is used
                        logger.info("Sample files are embedded in the .carxp project file")
                
                logger.info(f"Compressed session to: {zip_path}")
            
            logger.info(f"Saved session to: {path}")
            
            return {
                'success': True,
                'path': path,
                'file_size': file_size,
                'backup_path': backup_path,
                'compressed': compress,
                'plugin_count': self.carla.host.get_current_plugin_count(),
                'session_id': self.active_session
            }
            
        except Exception as e:
            logger.error(f"Failed to save session: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def create_snapshot(self, name: str, include_audio_files: bool = False,
                            session_context: dict = None, **kwargs) -> dict:
        """Create a session snapshot for A/B comparison
        
        Args:
            name: Snapshot name
            include_audio_files: Include audio files in snapshot
            
        Returns:
            Snapshot information
        """
        try:
            if not self.active_session:
                raise Exception("No active session")
            
            # Generate snapshot ID
            snapshot_id = str(uuid.uuid4())
            
            # Create snapshot directory
            snapshot_dir = self.session_dir / "snapshots" / snapshot_id
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            
            # Save current state to snapshot
            snapshot_path = snapshot_dir / f"{name}.carxp"
            self.carla.save_project(str(snapshot_path))
            
            # Get current plugin states
            plugin_states = []
            plugin_count = self.carla.host.get_current_plugin_count()
            
            for i in range(plugin_count):
                info = self.carla.host.get_plugin_info(i)
                if info:
                    # Get all parameter values
                    param_count = self.carla.host.get_parameter_count(i)
                    parameters = {}
                    
                    for p in range(param_count):
                        parameters[p] = self.carla.get_parameter(i, p)
                    
                    plugin_states.append({
                        'id': i,
                        'name': info['name'],
                        'active': self.carla.plugins.get(i, {}).get('active', False),
                        'volume': self.carla.plugins.get(i, {}).get('volume', 1.0),
                        'drywet': self.carla.plugins.get(i, {}).get('dry_wet', 1.0),
                        'parameters': parameters
                    })
            
            # Store snapshot info
            self.snapshots[snapshot_id] = {
                'id': snapshot_id,
                'name': name,
                'session_id': self.active_session,
                'created_at': datetime.now().isoformat(),
                'path': str(snapshot_path),
                'plugin_states': plugin_states,
                'include_audio': include_audio_files
            }
            
            # Save snapshot metadata
            metadata_path = snapshot_dir / "metadata.json"
            with open(metadata_path, 'w') as f:
                json.dump(self.snapshots[snapshot_id], f, indent=2)
            
            logger.info(f"Created snapshot {snapshot_id}: {name}")
            
            return {
                'success': True,
                'snapshot_id': snapshot_id,
                'name': name,
                'timestamp': self.snapshots[snapshot_id]['created_at'],
                'plugin_count': len(plugin_states)
            }
            
        except Exception as e:
            logger.error(f"Failed to create snapshot: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def switch_session(self, session_id: str, crossfade_ms: int = 0,
                           session_context: dict = None, **kwargs) -> dict:
        """Switch between sessions with optional crossfade
        
        Args:
            session_id: Session ID to switch to
            crossfade_ms: Crossfade duration in milliseconds
            
        Returns:
            Switch result
        """
        try:
            if session_id not in self.sessions:
                # Check if it's a snapshot ID
                if session_id in self.snapshots:
                    snapshot = self.snapshots[session_id]
                    # Load snapshot
                    success = self.carla.load_project(snapshot['path'])
                    
                    if success:
                        # Restore plugin states
                        for plugin_state in snapshot['plugin_states']:
                            plugin_id = plugin_state['id']
                            
                            # Restore parameters
                            for param_id, value in plugin_state['parameters'].items():
                                self.carla.set_parameter(plugin_id, int(param_id), value)
                            
                            # Restore other states
                            self.carla.set_plugin_active(plugin_id, plugin_state['active'])
                            # Set volume if the method exists (may need to use parameters instead)
                            if hasattr(self.carla.host, 'set_volume'):
                                self.carla.host.set_volume(plugin_id, plugin_state['volume'])
                            else:
                                self.carla.plugins[plugin_id]['volume'] = plugin_state['volume']
                            
                            # Set dry/wet mix
                            if hasattr(self.carla.host, 'set_drywet'):
                                self.carla.host.set_drywet(plugin_id, plugin_state['drywet'])
                            else:
                                self.carla.plugins[plugin_id]['dry_wet'] = plugin_state['drywet']
                        
                        logger.info(f"Switched to snapshot: {snapshot['name']}")
                        
                        return {
                            'success': True,
                            'active_session': session_id,
                            'type': 'snapshot',
                            'name': snapshot['name']
                        }
                else:
                    raise Exception(f"Session not found: {session_id}")
            
            session = self.sessions[session_id]
            
            # TODO: Implement crossfade if needed
            if crossfade_ms > 0:
                logger.info(f"Crossfading to session over {crossfade_ms}ms")
                # Implement crossfade logic
            
            # Load the session
            success = self.carla.load_project(session['path'])
            
            if not success:
                raise Exception(f"Failed to load session: {session['path']}")
            
            self.active_session = session_id
            
            logger.info(f"Switched to session: {session['name']}")
            
            return {
                'success': True,
                'active_session': session_id,
                'type': 'session',
                'name': session['name']
            }
            
        except Exception as e:
            logger.error(f"Failed to switch session: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def list_sessions(self, session_context: dict = None, **kwargs) -> dict:
        """List all available sessions and snapshots
        
        Returns:
            List of sessions and snapshots
        """
        try:
            sessions_list = []
            
            # Add loaded sessions
            for session_id, session in self.sessions.items():
                sessions_list.append({
                    'id': session_id,
                    'name': session['name'],
                    'type': 'session',
                    'path': session.get('path', ''),
                    'loaded_at': session.get('loaded_at', ''),
                    'is_active': session_id == self.active_session
                })
            
            # Add snapshots
            for snapshot_id, snapshot in self.snapshots.items():
                sessions_list.append({
                    'id': snapshot_id,
                    'name': snapshot['name'],
                    'type': 'snapshot',
                    'session_id': snapshot['session_id'],
                    'created_at': snapshot['created_at'],
                    'is_active': False
                })
            
            return {
                'success': True,
                'sessions': sessions_list,
                'active_session': self.active_session,
                'total_count': len(sessions_list)
            }
            
        except Exception as e:
            logger.error(f"Failed to list sessions: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def delete_session(self, session_id: str, session_context: dict = None, **kwargs) -> dict:
        """Delete a session or snapshot
        
        Args:
            session_id: Session or snapshot ID to delete
            
        Returns:
            Deletion result
        """
        try:
            if session_id in self.sessions:
                # Delete session
                session = self.sessions[session_id]
                
                if session_id == self.active_session:
                    raise Exception("Cannot delete active session")
                
                del self.sessions[session_id]
                logger.info(f"Deleted session: {session['name']}")
                
                return {
                    'success': True,
                    'deleted': session_id,
                    'type': 'session'
                }
                
            elif session_id in self.snapshots:
                # Delete snapshot
                snapshot = self.snapshots[session_id]
                
                # Remove snapshot files
                snapshot_dir = Path(snapshot['path']).parent
                if snapshot_dir.exists():
                    shutil.rmtree(snapshot_dir)
                
                del self.snapshots[snapshot_id]
                logger.info(f"Deleted snapshot: {snapshot['name']}")
                
                return {
                    'success': True,
                    'deleted': session_id,
                    'type': 'snapshot'
                }
            else:
                raise Exception(f"Session not found: {session_id}")
                
        except Exception as e:
            logger.error(f"Failed to delete session: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def export_session(self, session_id: str, export_path: str,
                           format: str = "carxp", session_context: dict = None, **kwargs) -> dict:
        """Export a session in various formats
        
        Args:
            session_id: Session ID to export
            export_path: Export destination
            format: Export format (carxp, ardour, reaper)
            
        Returns:
            Export result
        """
        try:
            if session_id not in self.sessions:
                raise Exception(f"Session not found: {session_id}")
            
            session = self.sessions[session_id]
            
            if format == "carxp":
                # Native Carla format
                shutil.copy2(session['path'], export_path)
            elif format == "ardour":
                # TODO: Implement Ardour session export
                raise NotImplementedError("Ardour export not yet implemented")
            elif format == "reaper":
                # TODO: Implement Reaper RPP export
                raise NotImplementedError("Reaper export not yet implemented")
            else:
                raise ValueError(f"Unknown export format: {format}")
            
            logger.info(f"Exported session to: {export_path}")
            
            return {
                'success': True,
                'export_path': export_path,
                'format': format,
                'file_size': os.path.getsize(export_path)
            }
            
        except Exception as e:
            logger.error(f"Failed to export session: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def import_session(self, import_path: str, format: str = "auto",
                           session_context: dict = None, **kwargs) -> dict:
        """Import a session from various formats
        
        Args:
            import_path: Path to import from
            format: Import format (auto-detect if "auto")
            
        Returns:
            Import result
        """
        try:
            # Auto-detect format
            if format == "auto":
                ext = Path(import_path).suffix.lower()
                if ext == ".carxp":
                    format = "carxp"
                elif ext in [".ardour", ".ardourx"]:
                    format = "ardour"
                elif ext == ".rpp":
                    format = "reaper"
                else:
                    raise ValueError(f"Unknown file format: {ext}")
            
            if format == "carxp":
                # Native Carla format - just load it
                return await self.load_session(import_path)
            elif format == "ardour":
                # TODO: Implement Ardour session import
                raise NotImplementedError("Ardour import not yet implemented")
            elif format == "reaper":
                # TODO: Implement Reaper RPP import
                raise NotImplementedError("Reaper import not yet implemented")
            else:
                raise ValueError(f"Unknown import format: {format}")
                
        except Exception as e:
            logger.error(f"Failed to import session: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }