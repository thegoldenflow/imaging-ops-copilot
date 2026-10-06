import pytest
from fastapi.testclient import TestClient

from app.core.store import reset_store
from app.llm.gateway import LlmGateway, set_gateway
from app.llm.providers import MockProvider


@pytest.fixture(autouse=True)
def fresh_state():
    reset_store()
    set_gateway(LlmGateway(MockProvider(latency_s=0)))
    yield
    set_gateway(None)


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
