import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from proxy import extract_usage


class TestExtractUsageNonStreaming:
    """Test non-streaming usage extraction for both API formats."""

    def test_openai_format(self):
        body = json.dumps({
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hello"}}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 50, "total_tokens": 75}
        }).encode()
        assert extract_usage(body, False) == (25, 50)

    def test_anthropic_format(self):
        body = json.dumps({
            "id": "msg_123",
            "type": "message",
            "content": [{"type": "text", "text": "Hello"}],
            "usage": {"input_tokens": 30, "output_tokens": 60}
        }).encode()
        assert extract_usage(body, False) == (30, 60)

    def test_no_usage_field(self):
        body = json.dumps({"id": "chatcmpl-123", "choices": []}).encode()
        assert extract_usage(body, False) == (0, 0)

    def test_empty_usage(self):
        body = json.dumps({"usage": {}}).encode()
        assert extract_usage(body, False) == (0, 0)

    def test_empty_bytes(self):
        assert extract_usage(b'', False) == (0, 0)

    def test_none_bytes(self):
        assert extract_usage(None, False) == (0, 0)

    def test_invalid_json(self):
        assert extract_usage(b'not json', False) == (0, 0)

    def test_error_response_no_usage(self):
        body = json.dumps({
            "error": {"message": "Model not found", "type": "error"}
        }).encode()
        assert extract_usage(body, False) == (0, 0)


class TestExtractUsageOpenAIStreaming:
    """Test OpenAI SSE streaming format."""

    def test_usage_in_final_chunk(self):
        events = (
            'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Hi"}}]}\n\n'
            'data: {"id":"chatcmpl-1","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n'
            'data: [DONE]\n\n'
        )
        assert extract_usage(events.encode(), True) == (10, 5)

    def test_no_usage_in_stream(self):
        events = (
            'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Hi"}}]}\n\n'
            'data: [DONE]\n\n'
        )
        assert extract_usage(events.encode(), True) == (0, 0)

    def test_empty_stream(self):
        assert extract_usage(b'', True) == (0, 0)


class TestExtractUsageAnthropicStreaming:
    """Test Anthropic SSE streaming format."""

    def test_message_start_and_delta(self):
        events = (
            'event: message_start\n'
            'data: {"type":"message_start","message":{"id":"msg_1","usage":{"input_tokens":25,"output_tokens":0}}}\n\n'
            'event: content_block_delta\n'
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}\n\n'
            'event: message_delta\n'
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":15}}\n\n'
            'event: message_stop\n'
            'data: {"type":"message_stop"}\n\n'
        )
        assert extract_usage(events.encode(), True) == (25, 15)

    def test_message_start_only(self):
        """If stream is cut short, we at least get input_tokens."""
        events = (
            'event: message_start\n'
            'data: {"type":"message_start","message":{"id":"msg_1","usage":{"input_tokens":40,"output_tokens":0}}}\n\n'
        )
        assert extract_usage(events.encode(), True) == (40, 0)

    def test_message_delta_output_tokens(self):
        """Final message_delta carries output_tokens."""
        events = (
            'event: message_start\n'
            'data: {"type":"message_start","message":{"id":"msg_1","usage":{"input_tokens":10,"output_tokens":0}}}\n\n'
            'event: message_delta\n'
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":100}}\n\n'
        )
        assert extract_usage(events.encode(), True) == (10, 100)

    def test_ping_events_ignored(self):
        events = (
            'event: ping\n'
            'data: {"type":"ping"}\n\n'
            'event: message_start\n'
            'data: {"type":"message_start","message":{"id":"msg_1","usage":{"input_tokens":5,"output_tokens":0}}}\n\n'
            'event: message_delta\n'
            'data: {"type":"message_delta","delta":{},"usage":{"output_tokens":3}}\n\n'
        )
        assert extract_usage(events.encode(), True) == (5, 3)
