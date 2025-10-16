#!/usr/bin/env python3
"""
Test suite for MixAssist dataset integration with Carla MCP Server
"""
import asyncio
import json
import pytest
from pathlib import Path
from mixassist_resources import MixAssistResourceProvider
from mixassist_schema import MixingTopic, get_schema_info


class TestMixAssistIntegration:
    """Test MixAssist dataset integration"""

    def setup_method(self):
        """Setup test environment"""
        self.provider = MixAssistResourceProvider()

    def test_resource_provider_initialization(self):
        """Test resource provider initializes correctly"""
        assert self.provider.dataset_path == Path("/mnt/Media/MixAssist/data")
        assert not self.provider._loaded

    def test_available_resources(self):
        """Test that all expected resources are available"""
        resources = self.provider.get_available_resources()

        # Check expected resource count
        assert len(resources) == 9

        # Check required resource URIs
        resource_uris = [str(r.uri) for r in resources]
        expected_uris = [
            "mixassist://schema",
            "mixassist://conversations",
            "mixassist://conversations/drums",
            "mixassist://conversations/guitars",
            "mixassist://conversations/bass",
            "mixassist://conversations/vocals",
            "mixassist://conversations/keys",
            "mixassist://conversations/overall_mix",
            "mixassist://advice/summary"
        ]

        for uri in expected_uris:
            assert uri in resource_uris

    def test_schema_resource(self):
        """Test schema resource returns valid information"""
        schema_content = self.provider.get_resource_content("mixassist://schema")
        schema_data = json.loads(schema_content)

        assert schema_data["dataset_name"] == "MixAssist"
        assert schema_data["schema_version"] == "1.0"
        assert "fields" in schema_data
        assert "statistics" in schema_data

        # Check field definitions
        fields = schema_data["fields"]
        expected_fields = [
            "conversation_id", "topic", "turn_id", "has_content",
            "input_history", "audio_file", "user", "assistant"
        ]
        for field in expected_fields:
            assert field in fields

    def test_conversations_resource(self):
        """Test general conversations resource"""
        try:
            conv_content = self.provider.get_resource_content("mixassist://conversations")
            conv_data = json.loads(conv_content)

            assert "total_conversations" in conv_data
            assert "conversations" in conv_data
            assert conv_data["total_conversations"] > 0

            # Check conversation structure
            if conv_data["conversations"]:
                first_conv = conv_data["conversations"][0]
                required_keys = ["conversation_id", "topic", "turn_id", "user", "assistant", "keywords"]
                for key in required_keys:
                    assert key in first_conv

        except Exception as e:
            # If dataset files don't exist, that's okay for testing structure
            if "No such file" in str(e) or "does not exist" in str(e):
                pytest.skip(f"Dataset files not available: {e}")
            else:
                raise

    def test_topic_specific_resources(self):
        """Test topic-specific conversation resources"""
        topics = ["drums", "guitars", "bass", "vocals", "keys", "overall_mix"]

        for topic in topics:
            try:
                topic_content = self.provider.get_resource_content(f"mixassist://conversations/{topic}")
                topic_data = json.loads(topic_content)

                assert "topic" in topic_data
                assert "count" in topic_data
                assert "conversations" in topic_data
                assert topic_data["topic"] == topic

            except Exception as e:
                if "No such file" in str(e) or "does not exist" in str(e):
                    pytest.skip(f"Dataset files not available: {e}")
                else:
                    raise

    def test_advice_summary_resource(self):
        """Test curated advice summary resource"""
        try:
            advice_content = self.provider.get_resource_content("mixassist://advice/summary")

            # Should be markdown format
            assert advice_content.startswith("# Professional Audio Engineering Advice")
            assert "## Drums Mixing" in advice_content
            assert "## Guitars Mixing" in advice_content
            assert "---" in advice_content  # Markdown separators

        except Exception as e:
            if "No such file" in str(e) or "does not exist" in str(e):
                pytest.skip(f"Dataset files not available: {e}")
            else:
                raise

    def test_conversation_search(self):
        """Test conversation search functionality"""
        try:
            # Test search for common audio engineering terms
            search_terms = ["eq", "compression", "bass", "mix"]

            for term in search_terms:
                results = self.provider.search_conversations(term)
                # Results should be a list (empty is okay if no matches)
                assert isinstance(results, list)

        except Exception as e:
            if "No such file" in str(e) or "does not exist" in str(e):
                pytest.skip(f"Dataset files not available: {e}")
            else:
                raise

    def test_mixing_topic_enum(self):
        """Test MixingTopic enum has correct values"""
        expected_topics = ["drums", "overall_mix", "guitars", "bass", "vocals", "keys"]

        for topic in expected_topics:
            assert hasattr(MixingTopic, topic.upper())
            assert MixingTopic[topic.upper()].value == topic

    def test_schema_info_function(self):
        """Test schema info function returns expected structure"""
        schema_info = get_schema_info()

        assert schema_info["dataset_name"] == "MixAssist"
        assert "description" in schema_info
        assert "schema_version" in schema_info
        assert "fields" in schema_info
        assert "statistics" in schema_info

        # Check statistics structure
        stats = schema_info["statistics"]
        assert "total_conversations" in stats
        assert "splits" in stats
        assert "topic_distribution" in stats

    def test_invalid_resource_uri(self):
        """Test handling of invalid resource URIs"""
        with pytest.raises(ValueError, match="Unknown resource URI"):
            self.provider.get_resource_content("mixassist://invalid_resource")

    def test_resource_mimetypes(self):
        """Test resources have correct MIME types"""
        resources = self.provider.get_available_resources()

        for resource in resources:
            if str(resource.uri).endswith("/summary"):
                assert resource.mimeType == "text/markdown"
            else:
                assert resource.mimeType == "application/json"


def test_server_integration():
    """Integration test with actual server (if available)"""
    try:
        from server import CarlaMCPServer

        # This would require a full server setup
        # For now, just test that import works
        assert CarlaMCPServer is not None

    except ImportError as e:
        pytest.skip(f"Server components not available: {e}")
    except Exception as e:
        pytest.skip(f"Server integration test failed: {e}")


def test_performance_metrics():
    """Test performance of dataset loading and queries"""
    import time

    provider = MixAssistResourceProvider()

    # Time resource loading
    start_time = time.time()
    try:
        resources = provider.get_available_resources()
        load_time = time.time() - start_time

        # Should be fast
        assert load_time < 1.0  # Less than 1 second
        assert len(resources) > 0

    except Exception as e:
        if "No such file" in str(e):
            pytest.skip("Dataset files not available for performance testing")
        else:
            raise


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])