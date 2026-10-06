import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from datasync import bilingual_terminology as terminology  # noqa: E402
from datasync.entity_alignment import schema_migration_statements  # noqa: E402


class BilingualTerminologyTests(unittest.TestCase):
    def test_reviewable_fixture_maps_zh_and_en_aliases_to_one_concept(self):
        concepts = terminology.load_terminology()
        records = list(terminology.iter_alias_records(concepts))
        headache_records = [
            record
            for record in records
            if record.entity_schema == "symptom"
            and record.canonical_name == "头痛"
        ]
        by_alias = {record.alias: record for record in headache_records}

        self.assertEqual(by_alias["头痛"].concept_id, by_alias["头疼"].concept_id)
        self.assertEqual(by_alias["头痛"].concept_id, by_alias["headache"].concept_id)
        self.assertEqual(by_alias["head pain"].canonical_name, "头痛")

    def test_fixture_is_small_and_covers_requested_entity_types(self):
        concepts = terminology.load_terminology()
        self.assertEqual(len(concepts), 6)
        self.assertEqual(
            {concept["entity_schema"] for concept in concepts},
            {"disease", "symptom", "department"},
        )
        self.assertTrue(
            all(concept["source"] == "phase1_reviewable_sample" for concept in concepts)
        )

    def test_migration_is_additive_and_does_not_invoke_neo4j(self):
        fixture_records = list(
            terminology.iter_alias_records(terminology.load_terminology())
        )
        concept_ids = {
            (record.entity_schema, record.canonical_name): record.concept_id
            for record in fixture_records
        }
        with (
            patch.object(terminology, "init_db") as init_db,
            patch.object(
                terminology,
                "upsert_reviewed_aliases",
                return_value=(10, 8, concept_ids),
            ) as upsert,
            patch.object(
                terminology, "index_multilingual_aliases", return_value=18
            ) as index,
        ):
            result = terminology.migrate_bilingual_terminology()

        init_db.assert_called_once_with()
        upsert.assert_called_once()
        index.assert_called_once()
        self.assertEqual(result["concepts"], 6)
        self.assertEqual(result["inserted"], 10)
        self.assertEqual(result["indexed"], 18)
        self.assertFalse(hasattr(terminology, "GraphDatabase"))
        migration_sql = " ".join(schema_migration_statements).lower()
        self.assertNotIn("drop ", migration_sql)
        self.assertNotIn("delete ", migration_sql)
        self.assertNotIn("truncate ", migration_sql)


if __name__ == "__main__":
    unittest.main()
