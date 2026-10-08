"""Phase 0 database acceptance: persistence, PHI ciphertext, append-only audit,
due-item claims that skip locked rows, and migrations that match the models."""

from datetime import datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db.crypto import cipher
from app.core.db.migrate import alembic_config
from app.core.db.repo import clear_process_cache
from app.core.store import ConnectionSource, Store, get_engine, get_store


def test_changes_survive_a_new_unit_of_work(fresh_state):
    store = get_store()
    appt = next(a for a in store.appointments.values() if a.status == "booked")
    appt.cancel_reason = "persistence check"
    store.save()

    # A new Store has no loaded objects, and without the process's row cache every read
    # goes back to the database.
    clear_process_cache()
    again = Store(ConnectionSource(store.conn()))
    assert again.appointments[appt.id].cancel_reason == "persistence check"


def test_row_cache_never_serves_an_old_row_version(fresh_state):
    store = get_store()
    appt = next(a for a in store.appointments.values() if a.status == "booked")  # now cached

    # Written behind the store's back, as another process would: the row gets a new xmin.
    store.conn().execute(text("UPDATE appointments SET cancel_reason = 'other process' WHERE row_key = :k"),
                         {"k": appt.id})
    second = Store(ConnectionSource(store.conn()))
    assert second.appointments[appt.id].cancel_reason == "other process"
    assert {a.id: a for a in second.appointments.values()}[appt.id].cancel_reason == "other process"

    # Two writes in one transaction share an xmin; the second must not be hidden by a
    # cached copy of the first.
    second.appointments[appt.id].cancel_reason = "first write"
    second.save()
    third = Store(ConnectionSource(store.conn()))
    assert len(third.appointments.values()) > 1000 and third.appointments[appt.id].cancel_reason == "first write"
    third.appointments[appt.id].cancel_reason = "second write"
    third.save()
    fourth = Store(ConnectionSource(store.conn()))
    assert fourth.appointments[appt.id].cancel_reason == "second write"
    assert {a.id: a for a in fourth.appointments.values()}[appt.id].cancel_reason == "second write"


def test_objects_from_the_row_cache_are_independent(fresh_state):
    store = get_store()
    entry_id = next(iter(store.waitlist.values())).id  # these loads fill the row cache
    record_id = next(iter(store.modules["dose_records"].values())).id

    second = Store(ConnectionSource(store.conn()))  # gets copies from the cache
    second.waitlist[entry_id].acceptable_site_ids.append("changed in place")  # list of strings
    second.modules["dose_records"][record_id].events[0].ctdivol_mgy = -1.0  # nested model; neither saved

    third = Store(ConnectionSource(store.conn()))
    assert "changed in place" not in third.waitlist[entry_id].acceptable_site_ids
    assert third.modules["dose_records"][record_id].events[0].ctdivol_mgy != -1.0


def test_phi_columns_are_ciphertext_in_sql(fresh_state):
    store = get_store()
    patient = next(iter(store.patients.values()))
    row = store.conn().execute(
        text("SELECT given_name, family_name, dob, health_card, dob_bidx FROM patients WHERE row_key = :k"),
        {"k": patient.id}).mappings().one()
    for column in ("given_name", "family_name", "dob", "health_card"):
        assert row[column].startswith("k1:")
        assert str(getattr(patient, column)) not in row[column]
    assert row["dob_bidx"] == cipher().blind_index(patient.dob.isoformat())
    assert store.patients.find_by("dob", patient.dob)  # lookups go through the blind index


def test_audit_table_refuses_update_and_delete(fresh_state):
    conn = get_store().conn()
    for statement in ("UPDATE audit_events SET reason = 'edited'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError), conn.begin_nested():
            conn.execute(text(statement))
    assert get_store().audit.verify() == (True, None)


def test_due_items_claimed_by_one_worker_are_skipped_by_another(fresh_state):
    later = datetime(2100, 1, 1)
    first = get_store().outbox.claim_due("scheduled_for", later, statuses=("scheduled",))
    assert first

    other_conn = get_engine().connect()
    try:
        other = Store(ConnectionSource(other_conn))
        second = other.outbox.claim_due("scheduled_for", later, statuses=("scheduled",))
        assert not {m.id for m in first} & {m.id for m in second}
    finally:
        other_conn.rollback()
        other_conn.close()


def test_migrations_match_the_models():
    command.check(alembic_config())  # raises when autogenerate would produce a new migration
