"""persist() writes a reset's rows with COPY instead of INSERT, because it runs under the demo-reset lock. Every table
must come out exactly as the INSERT left it: here the real generated data set is written both ways, inside the test's
transaction, and compared row by row in PostgreSQL's text form of the whole row (`t::text`), which also tells a SQL
NULL from a JSON null."""

from sqlalchemy import text

from app.core import store as store_module
from app.core.db import schema
from app.core.store import build_store, encode_rows, get_store, persist

# What COPY's text format has to escape (tab, newline, backslash, its NULL marker \N), and characters beyond ASCII.
AWKWARD = "tab\there, newline\nthere, backslash \\ and \\N, quote \" and ', é, 中文, 🙂"


def _rows_by_table(conn) -> dict[str, list[str]]:
    """Every data table's rows, each as the hash of its text form, sorted (seq is part of a row, so order counts)."""
    return {name: conn.execute(text(f'SELECT md5(t::text) AS h FROM "{name}" t ORDER BY h')).scalars().all()
            for name in schema.data_tables()}


def test_a_reset_written_with_copy_matches_one_written_with_insert(fresh_state, monkeypatch):
    store = get_store()
    conn = store.conn()
    data = build_store()
    rows = encode_rows(data)
    by_name = {table.name: encoded for table, encoded in rows}
    appointment = by_name["appointments"][0]
    appointment["cancel_reason"], appointment["risk_factors"] = AWKWARD, [AWKWARD, None, {"nested": [None, 1.5]}]
    by_name["appointments"][1]["risk_factors"] = None  # JSONB: written as JSON null, not SQL NULL
    by_name["module_state"].append({"name": "copy check", "data": None})
    by_name["images"].append({"row_key": "copy check", "data": bytes(range(256)) * 2, "media_type": AWKWARD})

    def insert_rows(cursor, dialect, table, encoded):  # what persist() did before COPY
        for i in range(0, len(encoded), 2000):
            conn.execute(table.insert(), encoded[i:i + 2000])

    with conn.begin_nested() as by_insert:
        with monkeypatch.context() as m:
            m.setattr(store_module, "_copy_rows", insert_rows)
            persist(data, store, rows)
        inserted = _rows_by_table(conn)
        by_insert.rollback()

    with conn.begin_nested() as by_copy:
        persist(data, store, rows)
        copied = _rows_by_table(conn)
        by_copy.rollback()

    assert len(inserted["fhir_resources"]) > 50_000 and len(inserted["appointments"]) > 1000
    assert {name: len(hashes) for name, hashes in copied.items()} == {n: len(h) for n, h in inserted.items()}
    assert [name for name in inserted if copied[name] != inserted[name]] == []
