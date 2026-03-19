#src/agents/tool_registry.py
import logging
from typing import List

from langchain_core.tools import BaseTool

from src.models.agent import AgentProfile

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Stateless resolver: maps an AgentProfile's allowed_tools list onto concrete
    BaseTool instances taken from an MCPPool slot's tool_dict.

    The registry itself holds no connections and owns no resources.
    All tool instances live in MCPPool slots and are acquired via MCPPool.acquire().
    """

    @staticmethod
    def resolve(profile: AgentProfile, tool_dict: dict[str, BaseTool]) -> List[BaseTool]:
        """
        Return only the tools listed in profile.allowed_tools, drawn from the
        provided tool_dict (a single MCPPool slot's tools).

        Raises RuntimeError if a declared tool is absent — fail-fast to catch
        config drift between AgentProfile and MCP server at startup.
        """
        resolved: List[BaseTool] = []
        for name in profile.allowed_tools:
            tool = tool_dict.get(name)
            if tool is None:
                available = sorted(tool_dict.keys())
                raise RuntimeError(
                    f"Config error: tool '{name}' declared in profile '{profile.name}' "
                    f"not found in MCP pool. Available: {available}"
                )
            resolved.append(tool)
        return resolved
