#!/usr/bin/env python3
"""
MixAssist Dataset Schema Definition for MCP Integration
"""
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from enum import Enum

class MixingTopic(Enum):
    DRUMS = "drums"
    OVERALL_MIX = "overall_mix"
    GUITARS = "guitars"
    BASS = "bass"
    VOCALS = "vocals"
    KEYS = "keys"

@dataclass
class InputHistoryItem:
    """Individual item in conversation input history"""
    audio_file: str
    content: str
    role: str  # 'user' or 'assistant'

@dataclass
class MixAssistConversation:
    """Single conversation turn in MixAssist dataset"""
    conversation_id: str
    topic: MixingTopic
    turn_id: int
    has_content: bool
    input_history: List[InputHistoryItem]
    audio_file: Optional[str]
    user: str
    assistant: str

    def get_full_context(self) -> str:
        """Get full conversation context including history"""
        context_parts = []

        # Add input history
        for item in self.input_history:
            context_parts.append(f"{item.role.title()}: {item.content}")

        # Add current turn
        context_parts.append(f"User: {self.user}")
        context_parts.append(f"Assistant: {self.assistant}")

        return "\n\n".join(context_parts)

    def get_engineering_advice(self) -> str:
        """Extract the engineering advice from assistant response"""
        return self.assistant

    def get_context_keywords(self) -> List[str]:
        """Extract relevant keywords for search/filtering"""
        keywords = [self.topic.value]

        # Common audio engineering terms
        text = f"{self.user} {self.assistant}".lower()

        audio_terms = [
            "eq", "compression", "reverb", "delay", "gate", "limiter",
            "frequency", "bass", "treble", "midrange", "dynamics",
            "stereo", "mono", "panning", "balance", "level", "gain",
            "distortion", "saturation", "harmonics", "phase",
            "automation", "bus", "send", "return", "sidechain"
        ]

        found_terms = [term for term in audio_terms if term in text]
        keywords.extend(found_terms)

        return list(set(keywords))

# Dataset statistics from analysis
DATASET_STATS = {
    "total_conversations": 640,
    "splits": {
        "train": 340,
        "test": 250,
        "validation": 50
    },
    "topic_distribution": {
        "drums": 138,
        "overall_mix": 93,
        "guitars": 58,
        "bass": 18,
        "vocals": 18,
        "keys": 15
    }
}

def get_schema_info() -> Dict[str, Any]:
    """Get complete schema information for MCP resource"""
    return {
        "dataset_name": "MixAssist",
        "description": "Professional audio engineering conversations and advice",
        "schema_version": "1.0",
        "fields": {
            "conversation_id": "Unique conversation identifier",
            "topic": "Audio mixing domain (drums, guitars, etc.)",
            "turn_id": "Sequential turn number in conversation",
            "has_content": "Whether turn contains meaningful content",
            "input_history": "Previous conversation context",
            "audio_file": "Referenced audio segment (metadata only)",
            "user": "Engineer's question or input",
            "assistant": "Expert mixing advice and response"
        },
        "statistics": DATASET_STATS
    }