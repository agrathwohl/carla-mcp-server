#!/usr/bin/env python3
"""
Data models for plugin learning system
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum


class TaskType(str, Enum):
    """Common audio engineering task types"""
    DRUM_COMPRESSION = "drum_compression"
    DRUM_EQ = "drum_eq"
    VOCAL_EQ = "vocal_eq"
    VOCAL_COMPRESSION = "vocal_compression"
    VOCAL_DEESSING = "vocal_deessing"
    BASS_COMPRESSION = "bass_compression"
    BASS_EQ = "bass_eq"
    GUITAR_EQ = "guitar_eq"
    GUITAR_COMPRESSION = "guitar_compression"
    PARALLEL_COMPRESSION = "parallel_compression"
    MULTIBAND_COMPRESSION = "multiband_compression"
    LIMITING = "limiting"
    REVERB = "reverb"
    DELAY = "delay"
    SATURATION = "saturation"
    STEREO_WIDENING = "stereo_widening"
    MASTERING = "mastering"
    OTHER = "other"


class MixingStage(str, Enum):
    """Mixing workflow stages"""
    TRACKING = "tracking"
    MIXING = "mixing"
    MASTERING = "mastering"
    SOUND_DESIGN = "sound_design"


@dataclass
class AudioContext:
    """Audio characteristics and context"""
    rms_before: Optional[float] = None
    rms_after: Optional[float] = None
    dynamic_range_before: Optional[float] = None
    dynamic_range_after: Optional[float] = None
    spectral_centroid: Optional[float] = None
    peak_level: Optional[float] = None
    source_material: Optional[str] = None  # e.g., "drums", "vocals", "bass"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AudioContext':
        return cls(**data)


@dataclass
class EffectivenessMetrics:
    """Metrics for evaluating plugin effectiveness"""
    usage_frequency: int = 0
    kept_in_final: int = 0  # How many times plugin was kept in final mix
    removed_count: int = 0  # How many times it was removed
    avg_duration_seconds: float = 0.0
    parameter_stability: float = 0.0  # Low variance = good
    user_ratings: List[float] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """Calculate success rate based on kept vs removed"""
        total = self.kept_in_final + self.removed_count
        return self.kept_in_final / total if total > 0 else 0.0

    @property
    def avg_rating(self) -> float:
        """Average user rating"""
        return sum(self.user_ratings) / len(self.user_ratings) if self.user_ratings else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "success_rate": self.success_rate,
            "avg_rating": self.avg_rating
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EffectivenessMetrics':
        # Remove computed properties if present
        data = {k: v for k, v in data.items() if k not in ('success_rate', 'avg_rating')}
        return cls(**data)


@dataclass
class PluginUsage:
    """Record of a plugin being used in a session"""
    plugin_id: str
    plugin_name: str
    task_type: TaskType
    loaded_at: str  # ISO format timestamp
    removed_at: Optional[str] = None
    kept_in_final: bool = False
    audio_context: Optional[AudioContext] = None
    parameters_snapshot: Dict[str, float] = field(default_factory=dict)
    parameter_changes: int = 0  # How many times params were adjusted
    user_rating: Optional[float] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.audio_context:
            data['audio_context'] = self.audio_context.to_dict()
        data['task_type'] = self.task_type.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PluginUsage':
        if 'audio_context' in data and data['audio_context']:
            data['audio_context'] = AudioContext.from_dict(data['audio_context'])
        if 'task_type' in data:
            data['task_type'] = TaskType(data['task_type'])
        return cls(**data)


@dataclass
class SessionRecord:
    """Complete session record"""
    session_id: str
    carla_project: Optional[str] = None
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
    end_time: Optional[str] = None
    duration_seconds: float = 0.0
    mixing_stage: MixingStage = MixingStage.MIXING
    plugins_used: List[PluginUsage] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['plugins_used'] = [p.to_dict() for p in self.plugins_used]
        data['mixing_stage'] = self.mixing_stage.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionRecord':
        if 'plugins_used' in data:
            data['plugins_used'] = [PluginUsage.from_dict(p) for p in data['plugins_used']]
        if 'mixing_stage' in data:
            data['mixing_stage'] = MixingStage(data['mixing_stage'])
        return cls(**data)


@dataclass
class PluginRecommendation:
    """Plugin recommendation with confidence score"""
    plugin_id: str
    plugin_name: str
    confidence: float  # 0.0 to 1.0
    evidence: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    suggested_params: Dict[str, float] = field(default_factory=dict)
    related_sessions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PluginRecommendation':
        return cls(**data)
