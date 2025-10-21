#!/usr/bin/env python3
"""
MixAssist MCP Resources for Carla MCP Server
Provides access to professional audio engineering conversations and advice
"""
import pandas as pd
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import asdict
from mixassist_schema import MixAssistConversation, MixingTopic, InputHistoryItem, get_schema_info
import mcp.types as types
import logging
from config_loader import get_config

logger = logging.getLogger(__name__)


class MixAssistResourceProvider:
    """Provides MCP resources for accessing MixAssist dataset"""

    def __init__(self, dataset_path: Optional[str] = None):
        """Initialize MixAssist resource provider

        Args:
            dataset_path: Path to dataset directory (optional, will use config if not provided)
        """
        # Load from config if not provided
        if dataset_path is None:
            config = get_config()
            dataset_path = config.get('MIXASSIST_DATASET_PATH')

            # Check if MixAssist is enabled
            self._enabled = config.get_bool('MIXASSIST_ENABLED', default=True)
        else:
            self._enabled = True

        self.dataset_path = Path(dataset_path) if dataset_path else None
        self._conversations: Optional[List[MixAssistConversation]] = None
        self._loaded = False
        self._available = self._check_availability()

        if not self._available:
            logger.warning(
                "MixAssist dataset not available. "
                "Run 'python setup_mixassist.py --download' to set it up."
            )

    def _check_availability(self) -> bool:
        """Check if dataset is available and valid

        Returns:
            True if dataset is available and can be loaded
        """
        if not self._enabled:
            logger.info("MixAssist resources disabled in configuration")
            return False

        if self.dataset_path is None:
            logger.info("MixAssist dataset path not configured")
            return False

        if not self.dataset_path.exists():
            logger.warning(f"MixAssist dataset path does not exist: {self.dataset_path}")
            return False

        # Check for required parquet files
        required_splits = ["train", "test", "validation"]
        for split in required_splits:
            parquet_file = self.dataset_path / f"{split}-00000-of-00001.parquet"
            if not parquet_file.exists():
                logger.warning(f"Missing MixAssist split: {parquet_file}")
                return False

        logger.info(f"MixAssist dataset available at: {self.dataset_path}")
        return True

    def is_available(self) -> bool:
        """Check if MixAssist resources are available

        Returns:
            True if resources can be provided
        """
        return self._available

    def _load_dataset(self) -> None:
        """Load and parse the MixAssist dataset"""
        if self._loaded:
            return

        if not self._available:
            raise RuntimeError(
                "MixAssist dataset not available. "
                "Run 'python setup_mixassist.py --download' to set it up."
            )

        conversations = []

        # Load all parquet files
        for split in ["train", "test", "validation"]:
            parquet_file = self.dataset_path / f"{split}-00000-of-00001.parquet"
            if parquet_file.exists():
                df = pd.read_parquet(parquet_file)

                for _, row in df.iterrows():
                    # Parse input history
                    input_history = []
                    if row['input_history'] is not None and len(row['input_history']) > 0:
                        for item in row['input_history']:
                            if isinstance(item, dict):
                                input_history.append(InputHistoryItem(
                                    audio_file=item.get('audio_file', ''),
                                    content=item.get('content', ''),
                                    role=item.get('role', '')
                                ))

                    conversation = MixAssistConversation(
                        conversation_id=row['conversation_id'],
                        topic=MixingTopic(row['topic']),
                        turn_id=row['turn_id'],
                        has_content=row['has_content'],
                        input_history=input_history,
                        audio_file=row['audio_file'],
                        user=row['user'] or '',
                        assistant=row['assistant'] or ''
                    )
                    conversations.append(conversation)

        self._conversations = conversations
        self._loaded = True

    def get_available_resources(self) -> List[types.Resource]:
        """Get list of available MCP resources"""
        # Return empty list if dataset not available
        if not self._available:
            return []

        return [
            types.Resource(
                uri="mixassist://schema",
                name="MixAssist Dataset Schema",
                description="Schema and metadata for the MixAssist audio engineering dataset",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://index",
                name="Topic Index",
                description="List all topics with conversation counts (tiny, <1K tokens)",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://index/drums",
                name="Drum Conversation IDs",
                description="Just conversation IDs for drums (no content, <500 tokens)",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://index/guitars",
                name="Guitar Conversation IDs",
                description="Just conversation IDs for guitars (no content, <500 tokens)",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://index/bass",
                name="Bass Conversation IDs",
                description="Just conversation IDs for bass (no content, <500 tokens)",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://index/vocals",
                name="Vocal Conversation IDs",
                description="Just conversation IDs for vocals (no content, <500 tokens)",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://index/keys",
                name="Keys Conversation IDs",
                description="Just conversation IDs for keys (no content, <500 tokens)",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://index/overall_mix",
                name="Overall Mix Conversation IDs",
                description="Just conversation IDs for overall mix (no content, <500 tokens)",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://advice/drums/top5",
                name="Top 5 Drum Tips",
                description="Curated best drum mixing advice (<2K tokens)",
                mimeType="text/markdown"
            ),
            types.Resource(
                uri="mixassist://advice/guitars/top5",
                name="Top 5 Guitar Tips",
                description="Curated best guitar mixing advice (<2K tokens)",
                mimeType="text/markdown"
            ),
            types.Resource(
                uri="mixassist://advice/bass/top5",
                name="Top 5 Bass Tips",
                description="Curated best bass mixing advice (<2K tokens)",
                mimeType="text/markdown"
            ),
            types.Resource(
                uri="mixassist://advice/vocals/top5",
                name="Top 5 Vocal Tips",
                description="Curated best vocal mixing advice (<2K tokens)",
                mimeType="text/markdown"
            ),
            types.Resource(
                uri="mixassist://advice/keys/top5",
                name="Top 5 Keys Tips",
                description="Curated best keys mixing advice (<2K tokens)",
                mimeType="text/markdown"
            ),
            types.Resource(
                uri="mixassist://advice/overall_mix/top5",
                name="Top 5 Overall Mix Tips",
                description="Curated best overall mix advice (<2K tokens)",
                mimeType="text/markdown"
            ),
            types.Resource(
                uri="mixassist://search",
                name="Search Conversations",
                description="Search with query parameter, returns top 10 matches (<5K tokens). Use: mixassist://search?q={query}",
                mimeType="application/json"
            )
        ]

    def get_resource_content(self, uri: str) -> str:
        """Get content for a specific MCP resource"""
        if not self._available:
            return json.dumps({
                "error": "MixAssist dataset not available",
                "message": "Run 'python setup_mixassist.py --download' to download and configure the dataset",
                "uri": uri
            }, indent=2)

        self._load_dataset()

        if uri == "mixassist://schema":
            return json.dumps(get_schema_info(), indent=2)

        elif uri == "mixassist://index":
            return self._get_topic_index()

        elif uri.startswith("mixassist://index/"):
            topic = uri.split("/")[-1]
            return self._get_topic_conversation_ids(topic)

        elif uri.startswith("mixassist://advice/") and uri.endswith("/top5"):
            topic = uri.split("/")[-2]
            return self._get_top5_advice(topic)

        elif uri.startswith("mixassist://conversation/"):
            conv_id = uri.split("/")[-1]
            return self._get_single_conversation(conv_id)

        elif uri.startswith("mixassist://search"):
            # Parse query parameter
            if "?" in uri:
                query = uri.split("?q=")[-1] if "?q=" in uri else ""
                return self._search_conversations(query)
            else:
                return json.dumps({
                    "error": "Missing query parameter",
                    "usage": "mixassist://search?q={your_search_term}",
                    "example": "mixassist://search?q=compression"
                })

        else:
            raise ValueError(f"Unknown resource URI: {uri}")

    def _get_all_conversations_json(self) -> str:
        """Get all conversations as JSON"""
        conversations_data = []
        for conv in self._conversations:
            conversations_data.append({
                "conversation_id": conv.conversation_id,
                "topic": conv.topic.value,
                "turn_id": conv.turn_id,
                "user": conv.user,
                "assistant": conv.assistant,
                "keywords": conv.get_context_keywords()
            })

        return json.dumps({
            "total_conversations": len(conversations_data),
            "conversations": conversations_data
        }, indent=2)

    def _get_conversations_by_topic(self, topic: str) -> str:
        """Get conversations filtered by topic"""
        filtered_convs = [conv for conv in self._conversations if conv.topic.value == topic]

        conversations_data = []
        for conv in filtered_convs:
            conversations_data.append({
                "conversation_id": conv.conversation_id,
                "turn_id": conv.turn_id,
                "user": conv.user,
                "assistant": conv.assistant,
                "full_context": conv.get_full_context(),
                "keywords": conv.get_context_keywords()
            })

        return json.dumps({
            "topic": topic,
            "count": len(conversations_data),
            "conversations": conversations_data
        }, indent=2)

    def _get_curated_advice(self) -> str:
        """Generate curated mixing advice from conversations"""
        advice_by_topic = {}

        for topic in MixingTopic:
            topic_convs = [conv for conv in self._conversations if conv.topic == topic]
            advice_snippets = [conv.get_engineering_advice() for conv in topic_convs[:5]]  # Top 5
            advice_by_topic[topic.value] = advice_snippets

        markdown_content = "# Professional Audio Engineering Advice from MixAssist Dataset\n\n"

        for topic, advice_list in advice_by_topic.items():
            markdown_content += f"## {topic.replace('_', ' ').title()} Mixing\n\n"

            for i, advice in enumerate(advice_list, 1):
                markdown_content += f"**{i}.** {advice}\n\n"

            markdown_content += "---\n\n"

        return markdown_content

    def search_conversations(self, query: str) -> List[MixAssistConversation]:
        """Search conversations by text content"""
        self._load_dataset()

        query_lower = query.lower()
        matching_conversations = []

        for conv in self._conversations:
            # Search in user input, assistant response, and keywords
            search_text = f"{conv.user} {conv.assistant} {' '.join(conv.get_context_keywords())}".lower()

            if query_lower in search_text:
                matching_conversations.append(conv)

        return matching_conversations

    def _get_topic_index(self) -> str:
        """Get index of all topics with counts (tiny resource)"""
        index_data = {}
        for topic in MixingTopic:
            convs = [c for c in self._conversations if c.topic == topic]
            index_data[topic.value] = {
                "count": len(convs),
                "sample_ids": [c.conversation_id for c in convs[:3]]  # Just 3 sample IDs
            }

        return json.dumps({
            "total_conversations": len(self._conversations),
            "topics": index_data,
            "usage": "Use mixassist://index/{topic} to get all conversation IDs for a topic"
        }, indent=2)

    def _get_topic_conversation_ids(self, topic: str) -> str:
        """Get all conversation IDs for a topic (no content, just IDs)"""
        convs = [c for c in self._conversations if c.topic.value == topic]

        return json.dumps({
            "topic": topic,
            "count": len(convs),
            "conversation_ids": [c.conversation_id for c in convs],
            "usage": f"Use mixassist://conversation/{{conv_id}} to get full conversation content"
        }, indent=2)

    def _get_single_conversation(self, conv_id: str) -> str:
        """Get one full conversation by ID (single conversation only)"""
        conv = next((c for c in self._conversations if c.conversation_id == conv_id), None)
        if not conv:
            raise ValueError(f"Conversation {conv_id} not found")

        return json.dumps({
            "conversation_id": conv.conversation_id,
            "topic": conv.topic.value,
            "turn_id": conv.turn_id,
            "user": conv.user,
            "assistant": conv.assistant,
            "keywords": conv.get_context_keywords(),
            "full_context": conv.get_full_context()
        }, indent=2)

    def _get_top5_advice(self, topic: str) -> str:
        """Get curated top 5 mixing advice for a topic (small resource)"""
        topic_convs = [c for c in self._conversations if c.topic.value == topic]

        # Select top 5 by quality heuristics:
        # 1. Technical terminology usage = most valuable
        # 2. Longer assistant responses = more detailed advice
        # 3. More keywords = more technical content

        # High-value audio engineering terms
        premium_terms = [
            'eq', 'compression', 'compressor', 'limiting', 'limiter',
            'reverb', 'multiband', 'attack', 'release', 'threshold',
            'ratio', 'hz', 'khz', 'filter', 'frequency', 'db',
            'sidechain', 'parallel', 'automation', 'transient',
            'saturation', 'harmonics', 'phase', 'stereo', 'mono'
        ]

        scored_convs = []
        for conv in topic_convs:
            score = 0
            text = f"{conv.user} {conv.assistant}".lower()

            # Count premium technical terms (heavily weighted)
            premium_count = sum(1 for term in premium_terms if term in text)
            score += premium_count * 500

            # Longer responses get higher score
            score += len(conv.assistant) * 0.5

            # More keywords = more technical depth
            score += len(conv.get_context_keywords()) * 100

            # Penalize very short responses
            if len(conv.assistant) < 100:
                score *= 0.1

            scored_convs.append((score, conv))

        # Sort by score descending and take top 5
        scored_convs.sort(key=lambda x: x[0], reverse=True)
        top_convs = [conv for score, conv in scored_convs[:5]]

        markdown = f"# Top {len(top_convs)} {topic.replace('_', ' ').title()} Mixing Tips\n\n"
        markdown += f"**Selected from {len(topic_convs)} professional conversations**\n"
        markdown += f"*(Ranked by detail level and technical depth)*\n\n"

        for i, conv in enumerate(top_convs, 1):
            # Truncate long user questions for readability
            user_preview = conv.user[:80] + "..." if len(conv.user) > 80 else conv.user
            markdown += f"## {i}. {user_preview}\n\n"
            markdown += f"{conv.assistant}\n\n"
            markdown += f"*Keywords: {', '.join(conv.get_context_keywords()[:5])}*\n\n"
            markdown += "---\n\n"

        return markdown

    def _search_conversations(self, query: str, limit: int = 10) -> str:
        """Search conversations and return top matches (limited results)"""
        if not query:
            return json.dumps({
                "error": "Empty query",
                "usage": "Provide search term in query parameter"
            })

        query_lower = query.lower()
        scored_matches = []

        for conv in self._conversations:
            text = f"{conv.user} {conv.assistant}".lower()

            if query_lower in text:
                # Score by relevance
                score = 0
                # Exact phrase matches get higher score
                score += text.count(query_lower) * 100
                # Keywords match
                if query_lower in [k.lower() for k in conv.get_context_keywords()]:
                    score += 200
                # Length of response (more detail = higher score)
                score += len(conv.assistant) * 0.1

                scored_matches.append((score, conv))

        # Sort by relevance and take top N
        scored_matches.sort(key=lambda x: x[0], reverse=True)
        top_matches = scored_matches[:limit]

        results = []
        for score, conv in top_matches:
            results.append({
                "conversation_id": conv.conversation_id,
                "topic": conv.topic.value,
                "relevance_score": int(score),
                "user_preview": conv.user[:100] + "..." if len(conv.user) > 100 else conv.user,
                "assistant_preview": conv.assistant[:200] + "..." if len(conv.assistant) > 200 else conv.assistant,
                "keywords": conv.get_context_keywords()[:5]
            })

        return json.dumps({
            "query": query,
            "total_matches": len(scored_matches),
            "showing": len(results),
            "results": results,
            "usage": "Use mixassist://conversation/{conv_id} to get full conversation"
        }, indent=2)

# Global instance for use in MCP server
mixassist_provider = MixAssistResourceProvider()