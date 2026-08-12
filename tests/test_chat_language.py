import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from langchain_core.messages import AIMessageChunk  # noqa: E402

from backend.chat_service import ChatService, response_language_for_query  # noqa: E402


class FakeStreamingAgent:
    def __init__(self, response):
        self.response = response
        self.last_input = None

    def stream(self, agent_input, **_kwargs):
        self.last_input = agent_input
        yield AIMessageChunk(content=self.response), {}


class ChatLanguageTests(unittest.TestCase):
    def test_language_detection_for_chinese_english_and_mixed_queries(self):
        self.assertEqual(response_language_for_query("头痛怎么办？"), "zh")
        self.assertEqual(response_language_for_query("What causes headaches?"), "en")
        self.assertEqual(response_language_for_query("What causes 高血压?"), "en")

    def test_browser_locale_cannot_override_question_language(self):
        # Browser locale is not part of the backend request contract.
        for _browser_locale in ("en-CA", "zh-CN"):
            self.assertEqual(response_language_for_query("头痛怎么办？"), "zh")
            self.assertEqual(
                response_language_for_query("What causes a headache?"), "en"
            )

    def test_chinese_query_is_passed_to_agent_unchanged(self):
        service = ChatService()
        service.agent = FakeStreamingAgent("中文回答")
        with patch("backend.chat_service.config.AGENT_STREAM_OUTPUT", True):
            self.assertEqual(list(service.chat("头痛怎么办？", "session-zh")), ["中文回答"])
        self.assertEqual(
            service.agent.last_input,
            {"messages": [("user", "头痛怎么办？")]},
        )

    def test_english_question_can_stream_an_english_answer(self):
        service = ChatService()
        service.agent = FakeStreamingAgent("A headache can have several causes.")
        with patch("backend.chat_service.config.AGENT_STREAM_OUTPUT", True):
            output = list(service.chat("What causes a headache?", "session-en"))
        self.assertEqual(output, ["A headache can have several causes."])

    def test_english_not_ready_message_is_localized(self):
        service = ChatService()
        output = list(service.chat("What causes a headache?", "session-en"))
        self.assertEqual(
            output,
            ["The service is not ready yet. Please try again shortly."],
        )


if __name__ == "__main__":
    unittest.main()
