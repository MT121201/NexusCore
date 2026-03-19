"""Tests for FastAPI layer — no external services required."""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- /health ---

def test_health_returns_200(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


# --- /v1/execute validation (no Temporal needed) ---

def test_execute_returns_503_when_temporal_disconnected(client):
    """Forcing temporal_client = None must return 503, not 500."""
    import src.api.main as api_module
    original = api_module.temporal_client
    api_module.temporal_client = None
    try:
        payload = {"prompt": "List all database tables.", "user_id": "test-user"}
        resp = client.post("/v1/execute", json=payload)
        assert resp.status_code == 503
    finally:
        api_module.temporal_client = original


def test_execute_rejects_short_prompt(client):
    """Prompt < 10 chars must be rejected with 422 before hitting Temporal."""
    payload = {"prompt": "short", "user_id": "test-user"}
    resp = client.post("/v1/execute", json=payload)
    assert resp.status_code == 422


def test_execute_rejects_missing_user_id(client):
    payload = {"prompt": "List all database tables."}
    resp = client.post("/v1/execute", json=payload)
    assert resp.status_code == 422


def test_execute_rejects_missing_prompt(client):
    payload = {"user_id": "test-user"}
    resp = client.post("/v1/execute", json=payload)
    assert resp.status_code == 422


def test_execute_accepts_valid_request_when_temporal_connected(client):
    """With a mocked Temporal client, a valid request must return 202."""
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock()

    import src.api.main as api_module
    original = api_module.temporal_client
    api_module.temporal_client = mock_client

    try:
        payload = {"prompt": "List all database tables.", "user_id": "test-user"}
        resp = client.post("/v1/execute", json=payload)
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "accepted"
        assert "task_id" in body
    finally:
        api_module.temporal_client = original


def test_execute_returns_stable_task_id_for_explicit_idempotency_key(client):
    """Same idempotency_key must return same task_id."""
    import src.api.main as api_module
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock()
    api_module.temporal_client = mock_client

    try:
        fixed_key = "12345678-1234-5678-1234-567812345678"
        payload = {"prompt": "List all database tables.", "user_id": "u", "idempotency_key": fixed_key}
        resp = client.post("/v1/execute", json=payload)
        assert resp.status_code == 202
        assert resp.json()["task_id"] == fixed_key
    finally:
        api_module.temporal_client = None
