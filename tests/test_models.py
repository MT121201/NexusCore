"""Tests for Pydantic models — no external services required."""
import pytest
from uuid import UUID
from pydantic import ValidationError

from src.models.state import TaskRequest, AgentResponse, CriticResponse
from src.models.agent import AgentProfile


# --- TaskRequest ---

def test_task_request_valid():
    r = TaskRequest(prompt="List all database tables.", user_id="user-1")
    assert isinstance(r.idempotency_key, UUID)


def test_task_request_prompt_too_short():
    with pytest.raises(ValidationError):
        TaskRequest(prompt="short", user_id="user-1")


def test_task_request_explicit_idempotency_key():
    key = UUID("12345678-1234-5678-1234-567812345678")
    r = TaskRequest(prompt="List all database tables.", user_id="u", idempotency_key=key)
    assert r.idempotency_key == key


# --- AgentResponse (confidence gate) ---

def test_agent_response_valid():
    r = AgentResponse(analysis="routing to db", next_agents=["db_agent"], confidence_score=0.9)
    assert r.next_agents == ["db_agent"]


def test_agent_response_confidence_too_low():
    with pytest.raises(ValidationError):
        AgentResponse(analysis="x", next_agents=["db_agent"], confidence_score=0.5)


def test_agent_response_confidence_at_threshold():
    r = AgentResponse(analysis="x", next_agents=["critic"], confidence_score=0.7)
    assert r.confidence_score == pytest.approx(0.7)


def test_agent_response_confidence_above_1():
    with pytest.raises(ValidationError):
        AgentResponse(analysis="x", next_agents=[], confidence_score=1.1)


def test_agent_response_extra_field_forbidden():
    with pytest.raises(ValidationError):
        AgentResponse(analysis="x", next_agents=[], confidence_score=0.8, unknown_field="oops")


# --- CriticResponse ---

def test_critic_response_acceptable():
    r = CriticResponse(is_acceptable=True, feedback="Looks great.")
    assert r.is_acceptable is True


# --- AgentProfile ---

def test_agent_profile_immutable_tools():
    p = AgentProfile(name="test", system_prompt="You are a test.", allowed_tools=["tool_a", "tool_b"])
    assert "tool_a" in p.allowed_tools
