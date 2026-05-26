#!/usr/bin/env python3
"""
Plugin Learning System for Carla MCP Server
Learns which plugins are effective for specific audio engineering tasks
"""
from pathlib import Path

__version__ = "1.0.0"

# Default storage location
DEFAULT_LEARNING_DIR = Path.home() / ".carla-mcp" / "learning"

from .models import (
    PluginUsage,
    SessionRecord,
    AudioContext,
    EffectivenessMetrics,
    PluginRecommendation,
    TaskType,
    MixingStage
)
from .storage import LearningStorage
from .tracker import PluginEffectivenessTracker
from .recommendation import RecommendationEngine
from .learned_resources import LearnedPluginResourceProvider

__all__ = [
    "PluginUsage",
    "SessionRecord",
    "AudioContext",
    "EffectivenessMetrics",
    "PluginRecommendation",
    "TaskType",
    "MixingStage",
    "LearningStorage",
    "PluginEffectivenessTracker",
    "RecommendationEngine",
    "LearnedPluginResourceProvider",
    "DEFAULT_LEARNING_DIR"
]
