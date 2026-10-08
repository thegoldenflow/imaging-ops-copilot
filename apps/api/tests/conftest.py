"""Tests run against PostgreSQL (`docker compose up -d postgres`).

The session creates a fresh test database, applies the migrations and writes the
demo data once. Each test then runs inside one transaction that is rolled back
at the end, so every test starts from the same seeded state. The test body and
the requests it makes share one store; after each request it is flushed and
emptied, so later reads come from the database.
"""

import base64
import os

os.environ.setdefault("TEST_DATABASE_URL", "postgresql+psycopg://ioc:ioc@127.0.0.1:5433/ioc_test")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["PHI_ENCRYPTION_KEY"] = base64.b64encode(b"test-only-phi-encryption-key-32b").decode()
os.environ["PHI_BLIND_INDEX_KEY"] = base64.b64encode(b"test-only-phi-blind-index-key-32").decode()
os.environ["BACKGROUND_WORKERS"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.db.migrate import upgrade  # noqa: E402
from app.core.db.repo import clear_process_cache  # noqa: E402
from app.core.store import ConnectionSource, Store, get_engine, reset_store, set_ambient_store, unit_of_work  # noqa: E402
from app.llm.gateway import LlmGateway, set_gateway  # noqa: E402
from app.llm.providers import MockProvider  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    url = make_url(os.environ["TEST_DATABASE_URL"])
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()
    upgrade()
    with unit_of_work():
        reset_store()
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def fresh_state(database):
    conn = get_engine().connect()
    outer = conn.begin()
    store = Store(ConnectionSource(conn))
    set_ambient_store(store)
    set_gateway(LlmGateway(MockProvider(latency_s=0)))
    yield store
    set_gateway(None)
    set_ambient_store(None)
    store.expunge()
    outer.rollback()
    conn.close()
    clear_process_cache()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def login(client):
    def _login(user_id: str) -> dict:
        token = client.post("/api/auth/login", json={"user_id": user_id}).json()["token"]
        return {"Authorization": f"Bearer {token}"}

    return _login
