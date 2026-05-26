#!/usr/bin/env python3
"""
Learned Plugin MCP Resources for Carla MCP Server
Provides access to learned plugin effectiveness data via MCP resources
"""
import json
import logging
from typing import List, Optional
from urllib.parse import parse_qs, urlparse
import mcp.types as types

from .models import TaskType, AudioContext
from .storage import LearningStorage
from .recommendation import RecommendationEngine

logger = logging.getLogger(__name__)


class LearnedPluginResourceProvider:
    """Provides MCP resources for accessing learned plugin data"""

    def __init__(self, storage: Optional[LearningStorage] = None):
        """Initialize learned plugin resource provider

        Args:
            storage: LearningStorage instance (creates default if None)
        """
        self.storage = storage or LearningStorage()
        self.recommendation_engine = RecommendationEngine(self.storage)
        self._enabled = True
        self._check_storage_availability()

        logger.info(f"Learned plugin resources initialized at: {self.storage.data_dir}")

    def _check_storage_availability(self):
        """Check if storage is available and has data"""
        try:
            history = self.storage.get_plugin_history()
            total_sessions = history.get('metadata', {}).get('total_sessions', 0)

            if total_sessions == 0:
                logger.info("No learning data yet - system ready to start learning")
            else:
                logger.info(f"Learning data available: {total_sessions} sessions")

        except Exception as e:
            logger.warning(f"Could not check learning data: {e}")

    def is_available(self) -> bool:
        """Check if learned resources are available

        Returns:
            True if resources can be provided
        """
        return self._enabled

    def get_available_resources(self) -> List[types.Resource]:
        """Get list of available MCP resources"""
        return [
            types.Resource(
                uri="learned://index",
                name="Learning System Index",
                description="Overview of learned plugin effectiveness data (tiny, <1K tokens)",
                mimeType="application/json"
            ),
            types.Resource(
                uri="learned://recommend/{task_type}",
                name="Task-Based Recommendations",
                description="Get plugin recommendations for specific task. Use: learned://recommend/drum_compression",
                mimeType="application/json"
            ),
            types.Resource(
                uri="learned://plugin/{plugin_id}",
                name="Plugin Insights",
                description="Detailed effectiveness insights for a specific plugin",
                mimeType="application/json"
            ),
            types.Resource(
                uri="learned://search",
                name="Search Learned Patterns",
                description="Search learned data. Use: learned://search?q={query}",
                mimeType="application/json"
            ),
            types.Resource(
                uri="learned://recent",
                name="Recent Successes",
                description="Recent successful plugin uses (last 10 sessions)",
                mimeType="application/json"
            ),
            types.Resource(
                uri="learned://tasks",
                name="Task Summary",
                description="Summary of all learned tasks and their top plugins",
                mimeType="application/json"
            )
        ]

    def get_resource_content(self, uri: str) -> str:
        """Get content for a specific MCP resource

        Args:
            uri: Resource URI to fetch

        Returns:
            JSON or text content for the resource
        """
        try:
            if uri == "learned://index":
                return self._get_index()

            elif uri.startswith("learned://recommend/"):
                task_str = uri.split("/")[-1]
                # Handle query parameters
                if "?" in task_str:
                    task_str = task_str.split("?")[0]
                return self._get_recommendations(task_str, uri)

            elif uri.startswith("learned://plugin/"):
                plugin_id = uri.split("/", 3)[-1]  # Handle plugin IDs with slashes
                return self._get_plugin_insights(plugin_id)

            elif uri.startswith("learned://search"):
                query = self._extract_query_param(uri, "q")
                return self._search_learned_data(query)

            elif uri == "learned://recent":
                limit = int(self._extract_query_param(uri, "limit", "10"))
                return self._get_recent_successes(limit)

            elif uri == "learned://tasks":
                return self._get_task_summary()

            else:
                return json.dumps({
                    "error": f"Unknown resource URI: {uri}",
                    "available_patterns": [
                        "learned://index",
                        "learned://recommend/{task_type}",
                        "learned://plugin/{plugin_id}",
                        "learned://search?q={query}",
                        "learned://recent",
                        "learned://tasks"
                    ]
                }, indent=2)

        except Exception as e:
            logger.error(f"Error fetching resource {uri}: {e}")
            return json.dumps({
                "error": str(e),
                "uri": uri
            }, indent=2)

    def _extract_query_param(self, uri: str, param: str, default: str = "") -> str:
        """Extract query parameter from URI"""
        if "?" not in uri:
            return default

        parsed = urlparse(uri)
        params = parse_qs(parsed.query)
        return params.get(param, [default])[0]

    def _get_index(self) -> str:
        """Get overview of learning system"""
        history = self.storage.get_plugin_history()
        metadata = history.get('metadata', {})
        plugins = history.get('plugins', {})
        tasks = history.get('tasks', {})

        # Build task summary
        task_summary = {}
        for task_key, task_data in tasks.items():
            task_summary[task_key] = {
                "total_uses": task_data.get('total_uses', 0),
                "unique_plugins": len(task_data.get('top_plugins', {}))
            }

        # Top plugins overall
        top_plugins = sorted(
            plugins.items(),
            key=lambda x: x[1].get('total_uses', 0),
            reverse=True
        )[:5]

        return json.dumps({
            "summary": {
                "total_sessions": metadata.get('total_sessions', 0),
                "last_updated": metadata.get('last_updated', 'Never'),
                "total_plugins_tracked": len(plugins),
                "tasks_learned": len(tasks)
            },
            "tasks": task_summary,
            "top_plugins": [
                {
                    "plugin_id": pid,
                    "name": data.get('name', 'Unknown'),
                    "uses": data.get('total_uses', 0),
                    "success_rate": data.get('kept_in_final', 0) / max(1, data.get('total_uses', 1))
                }
                for pid, data in top_plugins
            ],
            "usage": "Use learned://recommend/{task} to get recommendations for a specific task"
        }, indent=2)

    def _get_recommendations(self, task_str: str, full_uri: str) -> str:
        """Get plugin recommendations for a task"""
        try:
            task = TaskType(task_str)
        except ValueError:
            # List valid task types
            valid_tasks = [t.value for t in TaskType]
            return json.dumps({
                "error": f"Invalid task type: {task_str}",
                "valid_tasks": valid_tasks,
                "example": "learned://recommend/drum_compression"
            }, indent=2)

        # Parse optional context parameters
        audio_context = None
        context_params = {}

        if "?" in full_uri:
            # Extract context hints
            for param in ['rms', 'dynamic_range', 'source']:
                value = self._extract_query_param(full_uri, param)
                if value:
                    context_params[param] = value

            if context_params:
                audio_context = AudioContext(
                    rms_before=float(context_params.get('rms', 0)) if 'rms' in context_params else None,
                    dynamic_range_before=float(context_params.get('dynamic_range', 0)) if 'dynamic_range' in context_params else None,
                    source_material=context_params.get('source')
                )

        # Get recommendations
        recommendations = self.recommendation_engine.recommend_for_task(task, audio_context, limit=5)

        if not recommendations:
            return json.dumps({
                "task": task.value,
                "recommendations": [],
                "message": "No learning data available for this task yet. Use plugins for this task and the system will learn!",
                "suggestion": "Check learned://tasks to see which tasks have learning data"
            }, indent=2)

        return json.dumps({
            "task": task.value,
            "context_provided": bool(audio_context),
            "recommendations": [
                {
                    "plugin_id": rec.plugin_id,
                    "plugin_name": rec.plugin_name,
                    "confidence": round(rec.confidence, 3),
                    "evidence": rec.evidence,
                    "reasoning": rec.reasoning,
                    "suggested_params": rec.suggested_params,
                    "related_sessions": rec.related_sessions
                }
                for rec in recommendations
            ],
            "usage": f"Use learned://plugin/{{plugin_id}} for detailed insights about a specific plugin"
        }, indent=2)

    def _get_plugin_insights(self, plugin_id: str) -> str:
        """Get detailed insights about a specific plugin"""
        insights = self.recommendation_engine.get_plugin_insights(plugin_id)
        return json.dumps(insights, indent=2)

    def _search_learned_data(self, query: str) -> str:
        """Search through learned data"""
        if not query:
            return json.dumps({
                "error": "Empty query",
                "usage": "learned://search?q={search_term}",
                "examples": [
                    "learned://search?q=multiband",
                    "learned://search?q=compression"
                ]
            }, indent=2)

        query_lower = query.lower()
        results = {
            "matching_plugins": [],
            "matching_tasks": []
        }

        history = self.storage.get_plugin_history()

        # Search plugins
        for plugin_id, plugin_data in history.get('plugins', {}).items():
            name = plugin_data.get('name', '').lower()
            if query_lower in name or query_lower in plugin_id.lower():
                results["matching_plugins"].append({
                    "plugin_id": plugin_id,
                    "name": plugin_data.get('name'),
                    "total_uses": plugin_data.get('total_uses', 0),
                    "success_rate": plugin_data.get('kept_in_final', 0) / max(1, plugin_data.get('total_uses', 1))
                })

        # Search tasks
        for task_key, task_data in history.get('tasks', {}).items():
            if query_lower in task_key.lower():
                results["matching_tasks"].append({
                    "task": task_key,
                    "total_uses": task_data.get('total_uses', 0),
                    "top_plugin_count": len(task_data.get('top_plugins', {}))
                })

        return json.dumps({
            "query": query,
            "results": results,
            "total_matches": len(results["matching_plugins"]) + len(results["matching_tasks"])
        }, indent=2)

    def _get_recent_successes(self, limit: int) -> str:
        """Get recent successful plugin uses"""
        recent_sessions = self.storage.get_recent_sessions(limit=limit)

        successes = []
        for session in recent_sessions:
            for usage in session.plugins_used:
                if usage.kept_in_final:
                    successes.append({
                        "session_id": session.session_id,
                        "plugin_name": usage.plugin_name,
                        "plugin_id": usage.plugin_id,
                        "task": usage.task_type.value,
                        "timestamp": usage.loaded_at,
                        "user_rating": usage.user_rating,
                        "notes": usage.notes if usage.notes else None
                    })

        return json.dumps({
            "recent_successes": successes[:limit],
            "total": len(successes)
        }, indent=2)

    def _get_task_summary(self) -> str:
        """Get summary of all tasks and their top plugins"""
        history = self.storage.get_plugin_history()
        tasks = history.get('tasks', {})

        summary = {}
        for task_key, task_data in tasks.items():
            top_plugins = task_data.get('top_plugins', {})

            # Sort plugins by success count
            sorted_plugins = sorted(
                top_plugins.items(),
                key=lambda x: x[1].get('successes', 0),
                reverse=True
            )[:3]

            summary[task_key] = {
                "total_uses": task_data.get('total_uses', 0),
                "top_plugins": [
                    {
                        "plugin_id": pid,
                        "name": data.get('name'),
                        "uses": data.get('uses', 0),
                        "successes": data.get('successes', 0),
                        "success_rate": data.get('successes', 0) / max(1, data.get('uses', 1))
                    }
                    for pid, data in sorted_plugins
                ]
            }

        return json.dumps({
            "tasks": summary,
            "usage": "Use learned://recommend/{task} to get detailed recommendations"
        }, indent=2)


# Global instance
learned_provider = LearnedPluginResourceProvider()
