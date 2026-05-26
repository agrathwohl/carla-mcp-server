#!/usr/bin/env python3
"""
Recommendation engine for plugin selection
Scores and ranks plugins based on learned effectiveness
"""
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
from .models import (
    PluginRecommendation,
    TaskType,
    AudioContext,
    SessionRecord
)
from .storage import LearningStorage

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """Generates plugin recommendations based on learned data"""

    def __init__(self, storage: Optional[LearningStorage] = None):
        """Initialize recommendation engine

        Args:
            storage: LearningStorage instance
        """
        self.storage = storage or LearningStorage()

    def recommend_for_task(
        self,
        task: TaskType,
        audio_context: Optional[AudioContext] = None,
        limit: int = 5
    ) -> List[PluginRecommendation]:
        """Get plugin recommendations for a specific task

        Args:
            task: Task type to get recommendations for
            audio_context: Current audio characteristics for context matching
            limit: Maximum number of recommendations to return

        Returns:
            List of PluginRecommendation objects, sorted by confidence (high to low)
        """
        # Get relevant sessions
        sessions = self.storage.get_sessions_for_task(task, limit=50)

        if not sessions:
            logger.info(f"No learning data for task: {task.value}")
            return []

        # Collect plugin usage statistics
        plugin_stats = self._collect_plugin_stats(sessions, task)

        # Calculate scores for each plugin
        recommendations = []
        for plugin_id, stats in plugin_stats.items():
            score = self._calculate_plugin_score(
                stats=stats,
                audio_context=audio_context,
                total_sessions=len(sessions)
            )

            if score['confidence'] > 0.3:  # Minimum confidence threshold
                recommendation = PluginRecommendation(
                    plugin_id=plugin_id,
                    plugin_name=stats['name'],
                    confidence=score['confidence'],
                    evidence={
                        "usage_count": stats['usage_count'],
                        "success_rate": stats['success_rate'],
                        "avg_rating": stats['avg_rating'],
                        "recent_uses": stats['recent_uses']
                    },
                    reasoning=self._generate_reasoning(stats, score, audio_context),
                    suggested_params=stats.get('common_params', {}),
                    related_sessions=stats['session_ids'][:5]  # Top 5 most recent
                )
                recommendations.append(recommendation)

        # Sort by confidence
        recommendations.sort(key=lambda r: r.confidence, reverse=True)

        return recommendations[:limit]

    def _collect_plugin_stats(
        self,
        sessions: List[SessionRecord],
        task: TaskType
    ) -> Dict[str, Dict[str, Any]]:
        """Collect usage statistics for plugins in sessions

        Args:
            sessions: List of sessions to analyze
            task: Task type to filter by

        Returns:
            Dictionary mapping plugin_id to statistics
        """
        plugin_stats = {}

        for session in sessions:
            for usage in session.plugins_used:
                if usage.task_type != task:
                    continue

                plugin_id = usage.plugin_id

                if plugin_id not in plugin_stats:
                    plugin_stats[plugin_id] = {
                        'name': usage.plugin_name,
                        'usage_count': 0,
                        'kept_count': 0,
                        'removed_count': 0,
                        'ratings': [],
                        'audio_contexts': [],
                        'parameters': [],
                        'session_ids': [],
                        'timestamps': []
                    }

                stats = plugin_stats[plugin_id]
                stats['usage_count'] += 1
                stats['session_ids'].append(session.session_id)
                stats['timestamps'].append(usage.loaded_at)

                if usage.kept_in_final:
                    stats['kept_count'] += 1
                elif usage.removed_at:
                    stats['removed_count'] += 1

                if usage.user_rating is not None:
                    stats['ratings'].append(usage.user_rating)

                if usage.audio_context:
                    stats['audio_contexts'].append(usage.audio_context)

                if usage.parameters_snapshot:
                    stats['parameters'].append(usage.parameters_snapshot)

        # Calculate derived metrics
        for plugin_id, stats in plugin_stats.items():
            total = stats['kept_count'] + stats['removed_count']
            stats['success_rate'] = stats['kept_count'] / total if total > 0 else 0.0
            stats['avg_rating'] = sum(stats['ratings']) / len(stats['ratings']) if stats['ratings'] else 0.0

            # Count recent uses (last 30 days)
            cutoff = datetime.now() - timedelta(days=30)
            recent_count = sum(
                1 for ts in stats['timestamps']
                if datetime.fromisoformat(ts) > cutoff
            )
            stats['recent_uses'] = recent_count

            # Find common parameters (mode or average)
            stats['common_params'] = self._extract_common_params(stats['parameters'])

        return plugin_stats

    def _extract_common_params(self, param_snapshots: List[Dict[str, float]]) -> Dict[str, float]:
        """Extract common/typical parameter values

        Args:
            param_snapshots: List of parameter snapshots

        Returns:
            Dictionary with averaged parameter values
        """
        if not param_snapshots:
            return {}

        # Collect all param values
        param_values = {}
        for snapshot in param_snapshots:
            for param_id, value in snapshot.items():
                if param_id not in param_values:
                    param_values[param_id] = []
                param_values[param_id].append(value)

        # Average the values
        common_params = {}
        for param_id, values in param_values.items():
            common_params[param_id] = sum(values) / len(values)

        return common_params

    def _calculate_plugin_score(
        self,
        stats: Dict[str, Any],
        audio_context: Optional[AudioContext],
        total_sessions: int
    ) -> Dict[str, float]:
        """Calculate confidence score for a plugin

        Args:
            stats: Plugin statistics
            audio_context: Current audio context for matching
            total_sessions: Total sessions analyzed

        Returns:
            Dictionary with score components and final confidence
        """
        # Base score from usage frequency (normalized)
        usage_score = min(1.0, stats['usage_count'] / max(1, total_sessions * 0.5))

        # Success rate component
        success_score = stats['success_rate']

        # User rating component (normalized to 0-1)
        rating_score = stats['avg_rating'] / 10.0 if stats['avg_rating'] > 0 else 0.5

        # Recency bonus (prefer recently successful plugins)
        recency_score = min(1.0, stats['recent_uses'] / max(1, stats['usage_count']))

        # Context similarity (if audio context provided)
        context_score = 0.5  # Neutral default
        if audio_context and stats['audio_contexts']:
            context_score = self._calculate_context_similarity(
                audio_context,
                stats['audio_contexts']
            )

        # Weighted combination
        confidence = (
            usage_score * 0.25 +
            success_score * 0.30 +
            rating_score * 0.20 +
            recency_score * 0.10 +
            context_score * 0.15
        )

        return {
            'confidence': confidence,
            'usage_score': usage_score,
            'success_score': success_score,
            'rating_score': rating_score,
            'recency_score': recency_score,
            'context_score': context_score
        }

    def _calculate_context_similarity(
        self,
        current: AudioContext,
        historical: List[AudioContext]
    ) -> float:
        """Calculate how similar current context is to historical contexts

        Args:
            current: Current audio context
            historical: Historical audio contexts

        Returns:
            Similarity score 0.0 to 1.0
        """
        if not historical:
            return 0.5

        similarities = []

        for hist in historical:
            similarity = 0.0
            comparisons = 0

            # Compare dynamic range
            if current.dynamic_range_before is not None and hist.dynamic_range_before is not None:
                dr_diff = abs(current.dynamic_range_before - hist.dynamic_range_before)
                similarity += max(0, 1.0 - (dr_diff / 20.0))  # 20dB max difference
                comparisons += 1

            # Compare RMS levels
            if current.rms_before is not None and hist.rms_before is not None:
                rms_diff = abs(current.rms_before - hist.rms_before)
                similarity += max(0, 1.0 - (rms_diff / 20.0))  # 20dB max difference
                comparisons += 1

            # Compare spectral centroid
            if current.spectral_centroid is not None and hist.spectral_centroid is not None:
                sc_diff = abs(current.spectral_centroid - hist.spectral_centroid)
                similarity += max(0, 1.0 - (sc_diff / 10000.0))  # 10kHz max difference
                comparisons += 1

            # Compare source material
            if current.source_material and hist.source_material:
                if current.source_material.lower() == hist.source_material.lower():
                    similarity += 1.0
                comparisons += 1

            if comparisons > 0:
                similarities.append(similarity / comparisons)

        return sum(similarities) / len(similarities) if similarities else 0.5

    def _generate_reasoning(
        self,
        stats: Dict[str, Any],
        score: Dict[str, float],
        audio_context: Optional[AudioContext]
    ) -> str:
        """Generate human-readable reasoning for recommendation

        Args:
            stats: Plugin statistics
            score: Score components
            audio_context: Current audio context

        Returns:
            Reasoning string
        """
        parts = []

        # Usage frequency
        if stats['usage_count'] >= 5:
            parts.append(f"Used {stats['usage_count']} times")
        elif stats['usage_count'] >= 2:
            parts.append(f"Used {stats['usage_count']} times (limited data)")
        else:
            parts.append("Limited usage history")

        # Success rate
        if stats['success_rate'] >= 0.8:
            parts.append(f"{int(stats['success_rate'] * 100)}% success rate")
        elif stats['success_rate'] >= 0.6:
            parts.append(f"{int(stats['success_rate'] * 100)}% success rate (moderate)")
        elif stats['success_rate'] > 0:
            parts.append(f"{int(stats['success_rate'] * 100)}% success rate (low)")

        # User ratings
        if stats['avg_rating'] >= 8.0:
            parts.append(f"highly rated ({stats['avg_rating']:.1f}/10)")
        elif stats['avg_rating'] >= 6.0:
            parts.append(f"rated {stats['avg_rating']:.1f}/10")

        # Context matching
        if audio_context and score['context_score'] >= 0.7:
            parts.append("good match for current audio characteristics")
        elif audio_context and score['context_score'] >= 0.5:
            parts.append("moderate match for current audio")

        # Recent activity
        if stats['recent_uses'] >= 3:
            parts.append(f"used {stats['recent_uses']} times recently")

        return ", ".join(parts) if parts else "No specific data available"

    def get_plugin_insights(self, plugin_id: str) -> Dict[str, Any]:
        """Get detailed insights about a specific plugin

        Args:
            plugin_id: Plugin ID to analyze

        Returns:
            Dictionary with plugin insights
        """
        sessions = self.storage.get_sessions_for_plugin(plugin_id, limit=100)

        if not sessions:
            return {
                "error": "No usage data for this plugin",
                "plugin_id": plugin_id
            }

        # Collect stats across all tasks
        total_uses = 0
        kept_count = 0
        removed_count = 0
        ratings = []
        task_breakdown = {}

        for session in sessions:
            for usage in session.plugins_used:
                if usage.plugin_id != plugin_id:
                    continue

                total_uses += 1

                if usage.kept_in_final:
                    kept_count += 1
                elif usage.removed_at:
                    removed_count += 1

                if usage.user_rating:
                    ratings.append(usage.user_rating)

                # Track by task
                task_key = usage.task_type.value
                if task_key not in task_breakdown:
                    task_breakdown[task_key] = {"uses": 0, "successes": 0}

                task_breakdown[task_key]["uses"] += 1
                if usage.kept_in_final:
                    task_breakdown[task_key]["successes"] += 1

        # Calculate metrics
        success_rate = kept_count / (kept_count + removed_count) if (kept_count + removed_count) > 0 else 0.0
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0

        # Find best tasks
        best_tasks = []
        for task, data in task_breakdown.items():
            task_success_rate = data["successes"] / data["uses"] if data["uses"] > 0 else 0.0
            if data["uses"] >= 2 and task_success_rate >= 0.6:
                best_tasks.append({
                    "task": task,
                    "uses": data["uses"],
                    "success_rate": task_success_rate
                })

        best_tasks.sort(key=lambda x: (x["success_rate"], x["uses"]), reverse=True)

        return {
            "plugin_id": plugin_id,
            "plugin_name": sessions[0].plugins_used[0].plugin_name if sessions else "Unknown",
            "stats": {
                "total_uses": total_uses,
                "kept_in_final": kept_count,
                "removed": removed_count,
                "success_rate": success_rate,
                "avg_rating": avg_rating
            },
            "best_for": best_tasks[:5],
            "task_breakdown": task_breakdown
        }
