import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent.prompts import major_agent_system_prompt  # noqa: E402
from agent.tools_def import label_to_schema  # noqa: E402
from configuration import config  # noqa: E402
from datasync.entity_alignment import (  # noqa: E402
    EntityAlignment,
    LANGUAGE_EN,
    LANGUAGE_ZH,
    detect_alias_language,
    normalize_alias,
)


class EntityAlignmentMultilingualTests(unittest.TestCase):
    def setUp(self):
        # Bypass __init__: unit tests mock all external PostgreSQL/Chroma/model calls.
        self.aligner = EntityAlignment.__new__(EntityAlignment)

    @staticmethod
    def _connection_returning(*, fetchone=None, fetchall=None):
        cursor = MagicMock()
        cursor.fetchone.return_value = fetchone
        cursor.fetchall.return_value = fetchall or []
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor_context
        return connection, cursor

    def test_postgres_existing_chinese_mapping_is_unchanged(self):
        connection, cursor = self._connection_returning(
            fetchone={"std_name": "头痛"}
        )
        with patch(
            "datasync.entity_alignment._connect", return_value=connection
        ) as connect:
            result = self.aligner.entity_mapping("头疼", "symptom")

        self.assertEqual(result, "头痛")
        connect.assert_called_once_with()
        self.assertEqual(cursor.execute.call_args.args[1], ("头疼", "symptom"))

    def test_postgres_normalized_english_alias_maps_to_chinese_canonical(self):
        exact_connection, _ = self._connection_returning(fetchone=None)
        normalized_connection, normalized_cursor = self._connection_returning(
            fetchall=[{"std_name": "头痛"}]
        )
        with patch(
            "datasync.entity_alignment._connect",
            side_effect=[exact_connection, normalized_connection],
        ):
            result = self.aligner.entity_mapping("  HEADACHE  ", "symptom")

        self.assertEqual(result, "头痛")
        self.assertEqual(
            normalized_cursor.execute.call_args.args[1],
            ("headache", "symptom", "en"),
        )

    def test_chinese_entity_keeps_existing_canonical_mapping(self):
        with (
            patch.object(self.aligner, "entity_mapping", return_value="头痛") as exact,
            patch.object(self.aligner, "vector_retrieve") as zh_vector,
            patch.object(self.aligner, "multilingual_vector_retrieve") as multi_vector,
        ):
            self.assertEqual(self.aligner("头痛", "symptom"), "头痛")
        exact.assert_called_once_with("头痛", "symptom")
        zh_vector.assert_not_called()
        multi_vector.assert_not_called()

    def test_english_medical_term_resolves_to_same_canonical_entity(self):
        with (
            patch.object(self.aligner, "entity_mapping", return_value="头痛"),
            patch.object(self.aligner, "vector_retrieve") as zh_vector,
            patch.object(self.aligner, "multilingual_vector_retrieve") as multi_vector,
        ):
            self.assertEqual(self.aligner("headache", "symptom"), "头痛")
        zh_vector.assert_not_called()
        multi_vector.assert_not_called()

    def test_english_alias_resolves_deterministically(self):
        with (
            patch.object(self.aligner, "entity_mapping", return_value="头痛") as exact,
            patch.object(self.aligner, "vector_retrieve") as zh_vector,
        ):
            self.assertEqual(self.aligner("Head Pain", "symptom"), "头痛")
        exact.assert_called_once_with("Head Pain", "symptom")
        zh_vector.assert_not_called()

    def test_unknown_english_entity_does_not_use_chinese_embedding(self):
        with (
            patch.object(self.aligner, "entity_mapping", return_value=None),
            patch.object(self.aligner, "vector_retrieve") as zh_vector,
            patch.object(
                self.aligner, "multilingual_vector_retrieve", return_value=None
            ) as multi_vector,
            patch.object(self.aligner, "_cache_vector_candidate") as cache,
        ):
            self.assertIsNone(self.aligner("unknown syndrome xyz", "disease"))
        zh_vector.assert_not_called()
        multi_vector.assert_called_once_with("unknown syndrome xyz", "disease")
        cache.assert_not_called()

    def test_chinese_semantic_fallback_remains_on_existing_index(self):
        with (
            patch.object(self.aligner, "entity_mapping", return_value=None),
            patch.object(self.aligner, "vector_retrieve", return_value="头痛") as zh_vector,
            patch.object(self.aligner, "multilingual_vector_retrieve") as multi_vector,
            patch.object(self.aligner, "_cache_vector_candidate") as cache,
        ):
            self.assertEqual(self.aligner("头部疼痛", "symptom"), "头痛")
        zh_vector.assert_called_once_with("头部疼痛", where={"type": "symptom"})
        multi_vector.assert_not_called()
        cache.assert_called_once_with("头部疼痛", "symptom", "头痛", LANGUAGE_ZH)

    def test_alias_language_and_normalization(self):
        self.assertEqual(detect_alias_language("头疼"), LANGUAGE_ZH)
        self.assertEqual(detect_alias_language("Headache"), LANGUAGE_EN)
        self.assertEqual(normalize_alias("  Headache  ", LANGUAGE_EN), "headache")
        self.assertEqual(normalize_alias("头 痛", LANGUAGE_ZH), "头 痛")

    def test_department_is_supported_by_alignment_tool(self):
        self.assertEqual(label_to_schema["Department"], "department")

    def test_remaining_phase1_entity_types_are_not_silently_supported(self):
        for label in ("Way", "Prevent", "Treat", "Duration"):
            self.assertNotIn(label, label_to_schema)

    def test_vector_collection_names_are_fail_closed(self):
        with patch.object(
            config,
            "MULTILINGUAL_VECTOR_COLLECTION",
            config.CHINESE_VECTOR_COLLECTION,
        ):
            with self.assertRaises(ValueError):
                config.validate_vector_collection_isolation()

    def test_disabled_multilingual_model_does_not_touch_any_vector_collection(self):
        self.aligner.chroma_client = MagicMock()
        with patch.object(config, "MULTILINGUAL_EMBEDDING_MODEL_PATH", None):
            result = self.aligner.multilingual_vector_retrieve(
                "unknown syndrome", "disease"
            )
        self.assertIsNone(result)
        self.aligner.chroma_client.get_collection.assert_not_called()

    def test_chinese_vector_path_uses_only_chinese_collection(self):
        self.aligner.chroma_client = MagicMock()
        collection = self.aligner.chroma_client.get_collection.return_value
        collection.query.return_value = {
            "ids": [["symptom_headache"]],
            "documents": [["头痛"]],
            "distances": [[0.1]],
        }
        model = MagicMock()
        model.encode.return_value = [0.1, 0.2]
        with patch("datasync.entity_alignment.get_embedding_model", return_value=model):
            result = self.aligner.vector_retrieve(
                "头部疼痛", where={"type": "symptom"}
            )
        self.assertEqual(result, "头痛")
        self.aligner.chroma_client.get_collection.assert_called_once_with(
            config.CHINESE_VECTOR_COLLECTION
        )

    def test_prompt_requires_response_language_and_bilingual_alignment(self):
        self.assertIn("英文问题用英文回答，中文问题用中文回答", major_agent_system_prompt)
        self.assertIn("不要把“先翻译整个问题”当作实体对齐的替代方案", major_agent_system_prompt)
        # 界面语言标记优先于问题语言（详见 chat_service._agent_message）
        self.assertIn("Answer-Language", major_agent_system_prompt)
        self.assertIn("工具返回的任何提示或错误信息，必须先翻译成最终回答语言", major_agent_system_prompt)


if __name__ == "__main__":
    unittest.main()
