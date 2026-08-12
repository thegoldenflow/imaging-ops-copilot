import os
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from configuration import config  # noqa: E402
from datasync.bilingual_terminology import (  # noqa: E402
    AliasRecord,
    migrate_bilingual_terminology,
    upsert_reviewed_aliases,
)
from datasync.entity_alignment import EntityAlignment, init_db  # noqa: E402


AUDIT_POSTGRES_URI = os.getenv("AUDIT_POSTGRES_URI", "")


@unittest.skipUnless(
    AUDIT_POSTGRES_URI,
    "set AUDIT_POSTGRES_URI to an explicitly disposable local audit database",
)
class PostgresMigrationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = urlparse(AUDIT_POSTGRES_URI)
        if parsed.hostname not in {"127.0.0.1", "localhost"} or "audit" not in (
            parsed.path or ""
        ).lower():
            raise RuntimeError(
                "AUDIT_POSTGRES_URI must target a localhost database whose name contains 'audit'"
            )
        cls.original_uri = config.POSTGRES_URI
        config.POSTGRES_URI = AUDIT_POSTGRES_URI

    @classmethod
    def tearDownClass(cls):
        config.POSTGRES_URI = cls.original_uri

    def setUp(self):
        with psycopg.connect(AUDIT_POSTGRES_URI) as conn:
            with conn.cursor() as cur:
                cur.execute("drop table if exists entity_mapping")
                cur.execute(
                    """
                    create table entity_mapping (
                        id varchar(255) not null,
                        synonym varchar(255) not null,
                        std_name varchar(255) not null,
                        entity_schema varchar(255) not null,
                        is_reviewed integer not null default 0,
                        create_time timestamptz not null default now(),
                        update_time timestamptz,
                        primary key (synonym, entity_schema)
                    )
                    """
                )
                cur.executemany(
                    "insert into entity_mapping "
                    "(id, synonym, std_name, entity_schema, is_reviewed) "
                    "values (%s, %s, %s, %s, %s)",
                    [
                        ("headache-id", "头痛", "头痛", "symptom", 1),
                        ("headache-id", "头疼", "头痛", "symptom", 1),
                        ("nausea-id", "恶心", "恶心", "symptom", 1),
                        ("migraine-id", "偏头痛", "偏头痛", "disease", 1),
                        ("candidate-id", "头部不适", "头晕", "symptom", 0),
                    ],
                )
            conn.commit()

    def _rows(self):
        with psycopg.connect(AUDIT_POSTGRES_URI, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select id, synonym, std_name, entity_schema, is_reviewed, "
                    "language, normalized_synonym, source, match_confidence "
                    "from entity_mapping order by entity_schema, synonym"
                )
                return cur.fetchall()

    def test_old_schema_migrates_and_fixture_is_idempotent(self):
        init_db()
        migrated_rows = self._rows()

        self.assertEqual(len(migrated_rows), 5)
        self.assertTrue(
            all(row["language"] == "zh" for row in migrated_rows)
        )
        self.assertTrue(
            all(row["normalized_synonym"] == row["synonym"] for row in migrated_rows)
        )
        self.assertTrue(all(row["source"] == "legacy" for row in migrated_rows))

        with psycopg.connect(AUDIT_POSTGRES_URI, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select data_type from information_schema.columns "
                    "where table_name='entity_mapping' and column_name='synonym'"
                )
                self.assertEqual(cur.fetchone()["data_type"], "text")
                cur.execute(
                    """
                    select array_agg(att.attname order by key_column.ordinality) as columns
                    from pg_constraint con
                    cross join lateral unnest(con.conkey) with ordinality
                        as key_column(attnum, ordinality)
                    join pg_attribute att
                      on att.attrelid=con.conrelid and att.attnum=key_column.attnum
                    where con.conrelid='entity_mapping'::regclass and con.contype='p'
                    """
                )
                self.assertEqual(
                    list(cur.fetchone()["columns"]),
                    ["synonym_key", "entity_schema"],
                )

        # The schema migration itself is a no-op on the second run.
        before_second_schema_run = self._rows()
        init_db()
        self.assertEqual(self._rows(), before_second_schema_run)

        first = migrate_bilingual_terminology(build_vector_index=False)
        after_first_fixture = self._rows()
        second = migrate_bilingual_terminology(build_vector_index=False)
        self.assertEqual(self._rows(), after_first_fixture)
        self.assertGreater(first["inserted"], 0)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["updated"], 0)

        by_key = {
            (row["entity_schema"], row["synonym"]): row
            for row in after_first_fixture
        }
        self.assertEqual(by_key[("symptom", "headache")]["std_name"], "头痛")
        self.assertEqual(by_key[("symptom", "headache")]["language"], "en")
        self.assertEqual(by_key[("symptom", "headache")]["is_reviewed"], 1)
        self.assertEqual(by_key[("symptom", "headache")]["id"], "headache-id")
        self.assertEqual(by_key[("disease", "migraines")]["std_name"], "偏头痛")

        # A late conflict rolls back an earlier insertion in the same transaction.
        with self.assertRaises(ValueError):
            upsert_reviewed_aliases(
                [
                    AliasRecord(
                        "nausea-id",
                        "symptom",
                        "rollback-only-alias",
                        "en",
                        "恶心",
                        "audit",
                    ),
                    AliasRecord(
                        "wrong-id",
                        "symptom",
                        "HEADACHE",
                        "en",
                        "头晕",
                        "audit",
                    ),
                ]
            )
        after_conflict = self._rows()
        self.assertNotIn(
            "rollback-only-alias", {row["synonym"] for row in after_conflict}
        )
        self.assertEqual(
            next(row for row in after_conflict if row["synonym"] == "headache")[
                "std_name"
            ],
            "头痛",
        )

        # Vector candidates never overwrite a reviewed exact alias.
        aligner = EntityAlignment.__new__(EntityAlignment)
        aligner._cache_vector_candidate("headache", "symptom", "头晕", "en")
        aligner._cache_vector_candidate(
            "unreviewed-headache-candidate", "symptom", "头痛", "en"
        )
        rows = self._rows()
        self.assertEqual(
            next(row for row in rows if row["synonym"] == "headache")[
                "is_reviewed"
            ],
            1,
        )
        candidate = next(
            row
            for row in rows
            if row["synonym"] == "unreviewed-headache-candidate"
        )
        self.assertEqual(candidate["is_reviewed"], 0)
        self.assertEqual(candidate["source"], "vector_candidate")

    def test_unexpected_old_primary_key_rolls_back_entire_migration(self):
        with psycopg.connect(AUDIT_POSTGRES_URI) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update entity_mapping set id=id || synonym where id='headache-id'"
                )
                cur.execute(
                    "alter table entity_mapping drop constraint entity_mapping_pkey"
                )
                cur.execute(
                    "alter table entity_mapping add primary key (id, entity_schema)"
                )
            conn.commit()

        with self.assertRaises(RuntimeError):
            init_db()

        with psycopg.connect(AUDIT_POSTGRES_URI, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("select count(*) as count from entity_mapping")
                self.assertEqual(cur.fetchone()["count"], 5)
                cur.execute(
                    "select count(*) as count from information_schema.columns "
                    "where table_name='entity_mapping' and column_name='language'"
                )
                self.assertEqual(cur.fetchone()["count"], 0)


if __name__ == "__main__":
    unittest.main()
