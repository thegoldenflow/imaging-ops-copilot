"""Schema migrations (Alembic) and first-start seeding."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
_INIT_LOCK = 0x10C_1A17  # pg_advisory_xact_lock key: one process seeds, the others wait


def alembic_config() -> Config:
    from app.core.config import settings

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return cfg


def upgrade() -> None:
    command.upgrade(alembic_config(), "head")


def init_db() -> None:
    """Bring the schema up to date and generate the demo data if the database is empty."""
    from app.core.db.crypto import cipher
    from app.core.store import is_seeded, reset_store, unit_of_work

    cipher()  # fail at startup, not on the first patient read, when the PHI keys are missing
    upgrade()
    with unit_of_work() as store:
        store.conn().execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _INIT_LOCK})
        if not is_seeded(store):
            reset_store()
