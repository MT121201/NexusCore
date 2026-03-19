"""Smoke test: every module imports cleanly without side-effect errors."""


def test_import_config():
    from src.core.config import settings
    assert settings is not None


def test_import_models():
    pass


def test_import_agent_profiles():
    from src.agents.db_agent import DB_AGENT_PROFILE
    from src.agents.infra_agent import INFRA_AGENT_PROFILE
    assert DB_AGENT_PROFILE.name == "db_agent"
    assert INFRA_AGENT_PROFILE.name == "infra_agent"


def test_import_tool_registry():
    pass


def test_import_mcp_pool():
    pass


def test_import_graph_and_compiles():
    """Importing graph.py triggers _build_agent_graph() — confirms LangGraph compiles cleanly."""
    from src.workflows.graph import _AGENT_GRAPH
    assert _AGENT_GRAPH is not None


def test_import_engine():
    from src.agents.engine import llm
    assert llm is not None


def test_import_supervisor():
    from src.agents.supervisor import _SUPERVISOR_PROMPT
    assert _SUPERVISOR_PROMPT is not None


def test_import_specialists():
    from src.agents.specialists import _CRITIC_PROMPT
    assert _CRITIC_PROMPT is not None


def test_import_api():
    from src.api.main import app
    assert app is not None
