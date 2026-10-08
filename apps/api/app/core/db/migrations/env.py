"""Alembic environment. The target metadata is derived from the Pydantic models
(app/core/db/schema.py), so `alembic revision --autogenerate` picks up model changes."""

from alembic import context
from sqlalchemy import create_engine

import app.main  # noqa: F401  (imports every module, so all model classes resolve)
from app.core.config import settings
from app.core.db.schema import get_metadata

config = context.config
target_metadata = get_metadata()
URL = config.get_main_option("sqlalchemy.url") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(url=URL, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(URL)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
