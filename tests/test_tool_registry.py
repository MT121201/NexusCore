"""Tests for ToolRegistry.resolve() — no external services required."""
import pytest
from unittest.mock import MagicMock
from langchain_core.tools import BaseTool

from src.agents.tool_registry import ToolRegistry
from src.models.agent import AgentProfile


def _make_tool(name: str) -> BaseTool:
    t = MagicMock(spec=BaseTool)
    t.name = name
    return t


def test_resolve_returns_correct_tools():
    tool_dict = {
        "list_tables": _make_tool("list_tables"),
        "describe_table": _make_tool("describe_table"),
        "run_read_only_query": _make_tool("run_read_only_query"),
        "list_s3_buckets": _make_tool("list_s3_buckets"),
    }
    profile = AgentProfile(
        name="db_agent",
        system_prompt="...",
        allowed_tools=["list_tables", "describe_table"],
    )
    result = ToolRegistry.resolve(profile, tool_dict)
    assert len(result) == 2
    assert result[0].name == "list_tables"
    assert result[1].name == "describe_table"


def test_resolve_raises_on_missing_tool():
    tool_dict = {"list_tables": _make_tool("list_tables")}
    profile = AgentProfile(
        name="db_agent",
        system_prompt="...",
        allowed_tools=["list_tables", "run_read_only_query"],  # run_read_only_query missing
    )
    with pytest.raises(RuntimeError, match="run_read_only_query"):
        ToolRegistry.resolve(profile, tool_dict)


def test_resolve_empty_allowed_tools():
    tool_dict = {"list_tables": _make_tool("list_tables")}
    profile = AgentProfile(name="noop", system_prompt="...", allowed_tools=[])
    result = ToolRegistry.resolve(profile, tool_dict)
    assert result == []


def test_resolve_error_message_lists_available():
    tool_dict = {"tool_a": _make_tool("tool_a")}
    profile = AgentProfile(name="agent", system_prompt="...", allowed_tools=["missing_tool"])
    with pytest.raises(RuntimeError, match="tool_a"):
        ToolRegistry.resolve(profile, tool_dict)
