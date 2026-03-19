"""Tests for Settings — no external services required."""
import pytest
from src.core.config import Settings


def test_defaults_are_valid():
    s = Settings()
    assert s.temporal_host == "localhost:7233"
    assert s.temporal_task_queue == "nexuscore-task-queue"
    assert s.openai_model == "gpt-4o-mini"
    assert s.openai_max_retries == 3
    assert s.mcp_pool_size == 4
    assert 0 < s.supervisor_confidence_threshold <= 1.0
    assert s.temporal_max_concurrent_activities > 0
    assert s.temporal_max_concurrent_workflow_tasks > 0


def test_override_via_env(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    monkeypatch.setenv("MCP_POOL_SIZE", "8")
    monkeypatch.setenv("SUPERVISOR_CONFIDENCE_THRESHOLD", "0.85")
    s = Settings()
    assert s.openai_model == "gpt-4o"
    assert s.mcp_pool_size == 8
    assert s.supervisor_confidence_threshold == pytest.approx(0.85)
