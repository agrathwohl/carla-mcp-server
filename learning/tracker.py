#!/usr/bin/env python3
"""
Plugin effectiveness tracking
Monitors plugin usage and calculates effectiveness metrics
"""
import logging
from datetime import datetime
from typing import Dict, Optional, Any
from .models import (
    PluginUsage,
    SessionRecord,
    AudioContext,
    TaskType,
    MixingStage
)
from .storage import LearningStorage

logger = logging.getLogger(__name__)


class PluginEffectivenessTracker:
    """Tracks plugin usage and effectiveness"""

    def __init__(self, storage: Optional[LearningStorage] = None):
        """Initialize tracker

        Args:
            storage: LearningStorage instance (creates default if None)
        """
        self.storage = storage or LearningStorage()
        self.current_session: Optional[SessionRecord] = None
        self.active_plugins: Dict[int, PluginUsage] = {}  # plugin_id -> PluginUsage

    def start_session(self, carla_project: Optional[str] = None, mixing_stage: MixingStage = MixingStage.MIXING) -> str:
        """Start a new tracking session

        Args:
            carla_project: Path to Carla project file
            mixing_stage: Current mixing stage

        Returns:
            Session ID
        """
        session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")

        self.current_session = SessionRecord(
            session_id=session_id,
            carla_project=carla_project,
            mixing_stage=mixing_stage
        )

        logger.info(f"Started learning session: {session_id}")
        return session_id

    def record_plugin_loaded(
        self,
        plugin_id: str,
        plugin_name: str,
        carla_plugin_id: int,
        task_type: TaskType = TaskType.OTHER,
        audio_context: Optional[AudioContext] = None
    ) -> None:
        """Record when a plugin is loaded

        Args:
            plugin_id: Plugin URI or identifier
            plugin_name: Human-readable plugin name
            carla_plugin_id: Carla's internal plugin ID
            task_type: What task is this plugin being used for
            audio_context: Current audio characteristics
        """
        if not self.current_session:
            logger.warning("No active session - starting one automatically")
            self.start_session()

        usage = PluginUsage(
            plugin_id=plugin_id,
            plugin_name=plugin_name,
            task_type=task_type,
            loaded_at=datetime.now().isoformat(),
            audio_context=audio_context
        )

        self.active_plugins[carla_plugin_id] = usage
        logger.debug(f"Plugin loaded: {plugin_name} for {task_type.value}")

    def record_parameter_change(self, carla_plugin_id: int, param_id: int, value: float) -> None:
        """Record when a plugin parameter is changed

        Args:
            carla_plugin_id: Carla's plugin ID
            param_id: Parameter ID
            value: New parameter value
        """
        if carla_plugin_id in self.active_plugins:
            usage = self.active_plugins[carla_plugin_id]
            usage.parameters_snapshot[str(param_id)] = value
            usage.parameter_changes += 1

    def record_plugin_removed(
        self,
        carla_plugin_id: int,
        kept_in_final: bool = False,
        audio_context_after: Optional[AudioContext] = None
    ) -> None:
        """Record when a plugin is removed or session ends

        Args:
            carla_plugin_id: Carla's plugin ID
            kept_in_final: Was this plugin kept in the final mix?
            audio_context_after: Audio characteristics after processing
        """
        if carla_plugin_id not in self.active_plugins:
            logger.warning(f"Plugin {carla_plugin_id} not in active plugins")
            return

        usage = self.active_plugins[carla_plugin_id]
        usage.removed_at = datetime.now().isoformat()
        usage.kept_in_final = kept_in_final

        # Update audio context with 'after' measurements
        if audio_context_after and usage.audio_context:
            usage.audio_context.rms_after = audio_context_after.rms_before
            usage.audio_context.dynamic_range_after = audio_context_after.dynamic_range_before

        # Add to session
        if self.current_session:
            self.current_session.plugins_used.append(usage)

        # Remove from active
        del self.active_plugins[carla_plugin_id]

        logger.debug(f"Plugin removed: {usage.plugin_name}, kept={kept_in_final}")

    def add_user_rating(self, carla_plugin_id: int, rating: float, notes: str = "") -> None:
        """Add explicit user feedback for a plugin

        Args:
            carla_plugin_id: Carla's plugin ID
            rating: User rating (0-10)
            notes: Optional text notes
        """
        if carla_plugin_id in self.active_plugins:
            usage = self.active_plugins[carla_plugin_id]
            usage.user_rating = max(0.0, min(10.0, rating))  # Clamp to 0-10
            usage.notes = notes
            logger.info(f"User rated {usage.plugin_name}: {rating}/10")

    def end_session(self, save: bool = True) -> Optional[SessionRecord]:
        """End current session and optionally save

        Args:
            save: Whether to save session to storage

        Returns:
            SessionRecord if successful, None otherwise
        """
        if not self.current_session:
            logger.warning("No active session to end")
            return None

        # Mark any remaining active plugins as kept
        for carla_plugin_id in list(self.active_plugins.keys()):
            self.record_plugin_removed(carla_plugin_id, kept_in_final=True)

        # Finalize session
        self.current_session.end_time = datetime.now().isoformat()

        if self.current_session.start_time:
            start = datetime.fromisoformat(self.current_session.start_time)
            end = datetime.fromisoformat(self.current_session.end_time)
            self.current_session.duration_seconds = (end - start).total_seconds()

        # Save to storage
        if save:
            self.storage.save_session(self.current_session)
            logger.info(f"Session ended and saved: {self.current_session.session_id}")

        session = self.current_session
        self.current_session = None
        self.active_plugins = {}

        return session

    def get_current_session_summary(self) -> Dict[str, Any]:
        """Get summary of current session

        Returns:
            Dictionary with session information
        """
        if not self.current_session:
            return {"error": "No active session"}

        return {
            "session_id": self.current_session.session_id,
            "start_time": self.current_session.start_time,
            "active_plugins": len(self.active_plugins),
            "plugins_used": len(self.current_session.plugins_used),
            "mixing_stage": self.current_session.mixing_stage.value
        }

    def infer_task_from_context(
        self,
        plugin_name: str,
        audio_context: Optional[AudioContext] = None
    ) -> TaskType:
        """Attempt to infer task type from plugin name and context

        Args:
            plugin_name: Name of the plugin
            audio_context: Audio characteristics

        Returns:
            Inferred TaskType
        """
        name_lower = plugin_name.lower()

        # Check for specific plugin types
        if "compressor" in name_lower or "compress" in name_lower:
            if "multiband" in name_lower or "mb" in name_lower:
                return TaskType.MULTIBAND_COMPRESSION
            elif "parallel" in name_lower:
                return TaskType.PARALLEL_COMPRESSION
            else:
                # Try to infer from source material
                if audio_context and audio_context.source_material:
                    source = audio_context.source_material.lower()
                    if "drum" in source:
                        return TaskType.DRUM_COMPRESSION
                    elif "vocal" in source or "voice" in source:
                        return TaskType.VOCAL_COMPRESSION
                    elif "bass" in source:
                        return TaskType.BASS_COMPRESSION
                    elif "guitar" in source:
                        return TaskType.GUITAR_COMPRESSION

        elif "eq" in name_lower or "equaliz" in name_lower:
            if audio_context and audio_context.source_material:
                source = audio_context.source_material.lower()
                if "drum" in source:
                    return TaskType.DRUM_EQ
                elif "vocal" in source:
                    return TaskType.VOCAL_EQ
                elif "bass" in source:
                    return TaskType.BASS_EQ
                elif "guitar" in source:
                    return TaskType.GUITAR_EQ

        elif "limiter" in name_lower or "limit" in name_lower:
            return TaskType.LIMITING

        elif "reverb" in name_lower:
            return TaskType.REVERB

        elif "delay" in name_lower or "echo" in name_lower:
            return TaskType.DELAY

        elif "saturat" in name_lower or "distort" in name_lower:
            return TaskType.SATURATION

        elif "stereo" in name_lower or "width" in name_lower:
            return TaskType.STEREO_WIDENING

        elif "deess" in name_lower or "de-ess" in name_lower:
            return TaskType.VOCAL_DEESSING

        return TaskType.OTHER
