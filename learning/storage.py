#!/usr/bin/env python3
"""
Storage management for plugin learning data
Handles persistent storage in ~/.carla-mcp/learning/
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from .models import SessionRecord, PluginUsage, TaskType, EffectivenessMetrics

logger = logging.getLogger(__name__)


class LearningStorage:
    """Manages persistent storage of plugin learning data"""

    def __init__(self, data_dir: Optional[Path] = None):
        """Initialize storage

        Args:
            data_dir: Directory for learning data (default: ~/.carla-mcp/learning/)
        """
        if data_dir is None:
            data_dir = Path.home() / ".carla-mcp" / "learning"

        self.data_dir = Path(data_dir)
        self.sessions_dir = self.data_dir / "sessions"
        self.indexes_dir = self.data_dir / "indexes"

        # Create directories
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(exist_ok=True)
        self.indexes_dir.mkdir(exist_ok=True)

        # Main aggregate file
        self.history_file = self.data_dir / "plugin_history.json"

        # Initialize if needed
        if not self.history_file.exists():
            self._create_empty_history()

        logger.info(f"Learning storage initialized at: {self.data_dir}")

    def _create_empty_history(self):
        """Create empty history file"""
        empty_history = {
            "metadata": {
                "version": "1.0",
                "created": datetime.now().isoformat(),
                "total_sessions": 0,
                "last_updated": datetime.now().isoformat()
            },
            "plugins": {},
            "tasks": {}
        }
        self._write_json(self.history_file, empty_history)

    def _write_json(self, path: Path, data: Any):
        """Write JSON file safely"""
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    def _read_json(self, path: Path) -> Any:
        """Read JSON file"""
        with open(path, 'r') as f:
            return json.load(f)

    def save_session(self, session: SessionRecord) -> None:
        """Save a complete session record

        Args:
            session: SessionRecord to save
        """
        # Save individual session file
        session_file = self.sessions_dir / f"{session.session_id}.json"
        self._write_json(session_file, session.to_dict())

        # Update aggregate history
        self._update_history(session)

        # Rebuild indexes
        self._rebuild_indexes()

        logger.info(f"Session saved: {session.session_id}")

    def _update_history(self, session: SessionRecord) -> None:
        """Update aggregate plugin history with session data"""
        history = self._read_json(self.history_file)

        # Update metadata
        history['metadata']['total_sessions'] = history['metadata'].get('total_sessions', 0) + 1
        history['metadata']['last_updated'] = datetime.now().isoformat()

        # Update plugin stats
        for usage in session.plugins_used:
            plugin_id = usage.plugin_id

            if plugin_id not in history['plugins']:
                history['plugins'][plugin_id] = {
                    "name": usage.plugin_name,
                    "total_uses": 0,
                    "kept_in_final": 0,
                    "removed": 0,
                    "tasks": {}
                }

            plugin_data = history['plugins'][plugin_id]
            plugin_data['total_uses'] += 1

            if usage.kept_in_final:
                plugin_data['kept_in_final'] += 1
            elif usage.removed_at:
                plugin_data['removed'] += 1

            # Update task-specific stats
            task_key = usage.task_type.value
            if task_key not in plugin_data['tasks']:
                plugin_data['tasks'][task_key] = {
                    "count": 0,
                    "success_count": 0
                }

            plugin_data['tasks'][task_key]['count'] += 1
            if usage.kept_in_final:
                plugin_data['tasks'][task_key]['success_count'] += 1

        # Update task index
        for usage in session.plugins_used:
            task_key = usage.task_type.value

            if task_key not in history['tasks']:
                history['tasks'][task_key] = {
                    "total_uses": 0,
                    "top_plugins": {}
                }

            history['tasks'][task_key]['total_uses'] += 1

            # Track plugin usage for this task
            plugin_id = usage.plugin_id
            if plugin_id not in history['tasks'][task_key]['top_plugins']:
                history['tasks'][task_key]['top_plugins'][plugin_id] = {
                    "name": usage.plugin_name,
                    "uses": 0,
                    "successes": 0
                }

            history['tasks'][task_key]['top_plugins'][plugin_id]['uses'] += 1
            if usage.kept_in_final:
                history['tasks'][task_key]['top_plugins'][plugin_id]['successes'] += 1

        self._write_json(self.history_file, history)

    def _rebuild_indexes(self) -> None:
        """Rebuild fast lookup indexes"""
        # Index by task type
        task_index = {}

        # Index by plugin
        plugin_index = {}

        # Scan all session files
        for session_file in self.sessions_dir.glob("*.json"):
            session_data = self._read_json(session_file)
            session_id = session_data['session_id']

            for usage in session_data.get('plugins_used', []):
                task = usage['task_type']
                plugin_id = usage['plugin_id']

                # Update task index
                if task not in task_index:
                    task_index[task] = []
                if session_id not in task_index[task]:
                    task_index[task].append(session_id)

                # Update plugin index
                if plugin_id not in plugin_index:
                    plugin_index[plugin_id] = []
                if session_id not in plugin_index[plugin_id]:
                    plugin_index[plugin_id].append(session_id)

        # Write indexes
        self._write_json(self.indexes_dir / "by_task.json", task_index)
        self._write_json(self.indexes_dir / "by_plugin.json", plugin_index)

    def get_sessions_for_task(self, task: TaskType, limit: Optional[int] = None) -> List[SessionRecord]:
        """Get sessions that used plugins for a specific task

        Args:
            task: TaskType to filter by
            limit: Maximum number of sessions to return (most recent first)

        Returns:
            List of SessionRecord objects
        """
        index_file = self.indexes_dir / "by_task.json"
        if not index_file.exists():
            return []

        task_index = self._read_json(index_file)
        session_ids = task_index.get(task.value, [])

        # Load sessions (most recent first)
        sessions = []
        for session_id in reversed(session_ids[-limit:] if limit else session_ids):
            session_file = self.sessions_dir / f"{session_id}.json"
            if session_file.exists():
                session_data = self._read_json(session_file)
                sessions.append(SessionRecord.from_dict(session_data))

        return sessions

    def get_sessions_for_plugin(self, plugin_id: str, limit: Optional[int] = None) -> List[SessionRecord]:
        """Get sessions where a specific plugin was used

        Args:
            plugin_id: Plugin ID to filter by
            limit: Maximum number of sessions

        Returns:
            List of SessionRecord objects
        """
        index_file = self.indexes_dir / "by_plugin.json"
        if not index_file.exists():
            return []

        plugin_index = self._read_json(index_file)
        session_ids = plugin_index.get(plugin_id, [])

        sessions = []
        for session_id in reversed(session_ids[-limit:] if limit else session_ids):
            session_file = self.sessions_dir / f"{session_id}.json"
            if session_file.exists():
                session_data = self._read_json(session_file)
                sessions.append(SessionRecord.from_dict(session_data))

        return sessions

    def get_plugin_history(self) -> Dict[str, Any]:
        """Get complete plugin usage history

        Returns:
            Dictionary with plugin statistics
        """
        return self._read_json(self.history_file)

    def get_recent_sessions(self, limit: int = 10) -> List[SessionRecord]:
        """Get most recent sessions

        Args:
            limit: Number of recent sessions to return

        Returns:
            List of SessionRecord objects
        """
        session_files = sorted(self.sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

        sessions = []
        for session_file in session_files[:limit]:
            session_data = self._read_json(session_file)
            sessions.append(SessionRecord.from_dict(session_data))

        return sessions

    def cleanup_old_sessions(self, days: int = 90) -> int:
        """Remove session files older than specified days

        Args:
            days: Keep sessions from last N days

        Returns:
            Number of sessions deleted
        """
        cutoff = datetime.now() - timedelta(days=days)
        deleted = 0

        for session_file in self.sessions_dir.glob("*.json"):
            mtime = datetime.fromtimestamp(session_file.stat().st_mtime)
            if mtime < cutoff:
                session_file.unlink()
                deleted += 1

        if deleted > 0:
            # Rebuild history and indexes
            self._rebuild_history_from_sessions()
            self._rebuild_indexes()
            logger.info(f"Cleaned up {deleted} old sessions")

        return deleted

    def _rebuild_history_from_sessions(self) -> None:
        """Rebuild complete history from all session files"""
        self._create_empty_history()

        for session_file in self.sessions_dir.glob("*.json"):
            session_data = self._read_json(session_file)
            session = SessionRecord.from_dict(session_data)
            self._update_history(session)
