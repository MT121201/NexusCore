"""Tests for LangGraph routing functions — no external services required."""
from src.workflows.graph import route_from_supervisor, route_from_critic


# --- route_from_supervisor ---

def test_routes_to_db_agent():
    state = {"next_nodes": ["db_agent"]}
    assert route_from_supervisor(state) == ["db_agent"]


def test_routes_to_infra_agent():
    state = {"next_nodes": ["infra_agent"]}
    assert route_from_supervisor(state) == ["infra_agent"]


def test_routes_to_both_agents_parallel():
    state = {"next_nodes": ["db_agent", "infra_agent"]}
    result = route_from_supervisor(state)
    assert "db_agent" in result
    assert "infra_agent" in result


def test_routes_to_critic():
    state = {"next_nodes": ["critic"]}
    assert route_from_supervisor(state) == ["critic"]


def test_fallback_on_empty_next_nodes():
    state = {"next_nodes": []}
    assert route_from_supervisor(state) == ["fallback"]


def test_fallback_on_invalid_node():
    state = {"next_nodes": ["nonexistent_agent"]}
    assert route_from_supervisor(state) == ["fallback"]


def test_fallback_on_missing_key():
    state = {}
    assert route_from_supervisor(state) == ["fallback"]


def test_filters_out_invalid_mixed_with_valid():
    state = {"next_nodes": ["db_agent", "hallucinated_agent"]}
    result = route_from_supervisor(state)
    assert result == ["db_agent"]


# --- route_from_critic ---

def test_critic_routes_to_end():
    from langgraph.graph import END
    state = {"next_nodes": []}
    assert route_from_critic(state) == END


def test_critic_routes_back_to_supervisor():
    state = {"next_nodes": ["supervisor"]}
    assert route_from_critic(state) == "supervisor"


def test_critic_routes_to_end_on_missing_key():
    from langgraph.graph import END
    state = {}
    assert route_from_critic(state) == END
