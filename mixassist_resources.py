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

class MixAssistResourceProvider:
    """Provides MCP resources for accessing MixAssist dataset"""

    def __init__(self, dataset_path: str = "/mnt/Media/MixAssist/data"):
        self.dataset_path = Path(dataset_path)
        self._conversations: Optional[List[MixAssistConversation]] = None
        self._loaded = False

    def _load_dataset(self) -> None:
        """Load and parse the MixAssist dataset"""
        if self._loaded:
            return

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
        return [
            types.Resource(
                uri="mixassist://schema",
                name="MixAssist Dataset Schema",
                description="Schema and metadata for the MixAssist audio engineering dataset",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://conversations",
                name="All Conversations",
                description="Browse all professional audio engineering conversations",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://conversations/drums",
                name="Drum Mixing Conversations",
                description="Conversations focused on drum mixing techniques",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://conversations/guitars",
                name="Guitar Mixing Conversations",
                description="Conversations focused on guitar mixing techniques",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://conversations/bass",
                name="Bass Mixing Conversations",
                description="Conversations focused on bass mixing techniques",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://conversations/vocals",
                name="Vocal Mixing Conversations",
                description="Conversations focused on vocal mixing techniques",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://conversations/keys",
                name="Keys/Piano Mixing Conversations",
                description="Conversations focused on keyboard and piano mixing",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://conversations/overall_mix",
                name="Overall Mix Conversations",
                description="Conversations about overall mix balance and mastering",
                mimeType="application/json"
            ),
            types.Resource(
                uri="mixassist://advice/summary",
                name="Curated Mixing Advice",
                description="Summary of key mixing advice from expert conversations",
                mimeType="text/markdown"
            )
        ]

    def get_resource_content(self, uri: str) -> str:
        """Get content for a specific MCP resource"""
        self._load_dataset()

        if uri == "mixassist://schema":
            return json.dumps(get_schema_info(), indent=2)

        elif uri == "mixassist://conversations":
            return self._get_all_conversations_json()

        elif uri.startswith("mixassist://conversations/"):
            topic = uri.split("/")[-1]
            return self._get_conversations_by_topic(topic)

        elif uri == "mixassist://advice/summary":
            return self._get_curated_advice()

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

# Global instance for use in MCP server
mixassist_provider = MixAssistResourceProvider()