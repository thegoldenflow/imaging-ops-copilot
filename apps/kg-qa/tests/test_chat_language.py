import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from langchain_core.messages import AIMessageChunk  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from backend.chat_service import (  # noqa: E402
    ChatService,
    _agent_message,
    _strip_answer_language_marker,
    response_language_for_query,
)
from backend.schemas import Question  # noqa: E402


class FakeStreamingAgent:
    def __init__(self, response):
        self.response = response
        self.last_input = None

    def stream(self, agent_input, **_kwargs):
        self.last_input = agent_input
        yield AIMessageChunk(content=self.response), {}


class FailingAgent:
    def stream(self, agent_input, **_kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover - 使该方法成为生成器

    def invoke(self, agent_input, **_kwargs):
        raise RuntimeError("boom")


class ChatLanguageTests(unittest.TestCase):
    def test_language_detection_for_chinese_english_and_mixed_queries(self):
        # language 缺省时的回落路径
        self.assertEqual(response_language_for_query("头痛怎么办？"), "zh")
        self.assertEqual(response_language_for_query("What causes headaches?"), "en")
        self.assertEqual(response_language_for_query("What causes 高血压?"), "en")

    def test_agent_message_marker(self):
        self.assertEqual(_agent_message("头痛怎么办？", None), "头痛怎么办？")
        self.assertEqual(
            _agent_message("头痛怎么办？", "en"),
            "头痛怎么办？\n\n(Answer-Language: en)",
        )
        self.assertEqual(
            _agent_message("What causes a headache?", "zh"),
            "What causes a headache?\n\n(Answer-Language: zh)",
        )

    def test_explicit_language_overrides_question_language(self):
        # 界面所选语言优先于问题语言（新契约，取代旧的
        # test_browser_locale_cannot_override_question_language）
        service = ChatService()
        output = list(service.chat("头痛怎么办？", "s", language="en"))
        self.assertEqual(
            output, ["The service is not ready yet. Please try again shortly."]
        )
        output = list(service.chat("What causes a headache?", "s", language="zh"))
        self.assertEqual(output, ["服务尚未就绪，请稍后再试。"])

    def test_query_without_language_is_passed_to_agent_unchanged(self):
        # 向后兼容：老客户端不带 language，消息字节级不变
        service = ChatService()
        service.agent = FakeStreamingAgent("中文回答")
        with patch("backend.chat_service.config.AGENT_STREAM_OUTPUT", True):
            self.assertEqual(list(service.chat("头痛怎么办？", "session-zh")), ["中文回答"])
        self.assertEqual(
            service.agent.last_input,
            {"messages": [("user", "头痛怎么办？")]},
        )

    def test_query_with_language_gets_marker_appended(self):
        service = ChatService()
        service.agent = FakeStreamingAgent("An English answer.")
        with patch("backend.chat_service.config.AGENT_STREAM_OUTPUT", True):
            output = list(service.chat("头痛怎么办？", "session-en", language="en"))
        self.assertEqual(output, ["An English answer."])
        self.assertEqual(
            service.agent.last_input,
            {"messages": [("user", "头痛怎么办？\n\n(Answer-Language: en)")]},
        )

    def test_english_question_can_stream_a_chinese_answer_when_ui_is_chinese(self):
        service = ChatService()
        service.agent = FakeStreamingAgent("头痛可能有多种原因。")
        with patch("backend.chat_service.config.AGENT_STREAM_OUTPUT", True):
            output = list(
                service.chat("What causes a headache?", "session-zh", language="zh")
            )
        self.assertEqual(output, ["头痛可能有多种原因。"])
        self.assertEqual(
            service.agent.last_input,
            {"messages": [("user", "What causes a headache?\n\n(Answer-Language: zh)")]},
        )

    def test_english_question_can_stream_an_english_answer(self):
        service = ChatService()
        service.agent = FakeStreamingAgent("A headache can have several causes.")
        with patch("backend.chat_service.config.AGENT_STREAM_OUTPUT", True):
            output = list(service.chat("What causes a headache?", "session-en"))
        self.assertEqual(output, ["A headache can have several causes."])

    def test_english_not_ready_message_is_localized(self):
        # language 缺省时兜底文案仍按问题语言检测
        service = ChatService()
        output = list(service.chat("What causes a headache?", "session-en"))
        self.assertEqual(
            output,
            ["The service is not ready yet. Please try again shortly."],
        )

    def test_temporary_failure_uses_requested_language(self):
        service = ChatService()
        service.agent = FailingAgent()
        with patch("backend.chat_service.config.AGENT_STREAM_OUTPUT", True):
            output = list(service.chat("头痛怎么办？", "s", language="en"))
        self.assertEqual(
            output,
            ["The system is temporarily unable to respond. Please try again later."],
        )

    def test_marker_is_stripped_from_output_stream(self):
        # 模型偶尔会复述标记，服务端兜底剥除
        joined = "".join(
            _strip_answer_language_marker(
                ["I must answer in English ", "(Answer-Language: en)", " as asked."]
            )
        )
        self.assertEqual(joined, "I must answer in English  as asked.")

    def test_marker_split_across_token_chunks_is_stripped(self):
        # 流式下标记会被切分到多个块
        chunks = ["答案：", "(Answer", "-Lang", "uage:", " zh", ")", "痛风用药如下。"]
        joined = "".join(_strip_answer_language_marker(chunks))
        self.assertEqual(joined, "答案：痛风用药如下。")
        self.assertNotIn("Answer-Language", joined)

    def test_normal_parentheses_survive_stripping(self):
        chunks = ["常用药物（如布洛芬）", "和 NSAIDs (ibuprofen) ", "等。"]
        joined = "".join(_strip_answer_language_marker(chunks))
        self.assertEqual(joined, "常用药物（如布洛芬）和 NSAIDs (ibuprofen) 等。")

    def test_question_schema_language_field(self):
        self.assertIsNone(Question(message="hi").language)
        self.assertEqual(Question(message="hi", language="en").language, "en")
        self.assertEqual(Question(message="hi", language="zh").language, "zh")
        with self.assertRaises(ValidationError):
            Question(message="hi", language="fr")


if __name__ == "__main__":
    unittest.main()
