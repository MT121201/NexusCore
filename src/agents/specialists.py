# src/agents/specialists.py
import logging
from langchain_core.prompts import ChatPromptTemplate

from src.models.state import AgentState
from src.agents.engine import llm, broadcast, run_tool_loop
from src.agents.tool_registry import ToolRegistry
from src.agents.db_agent import DB_AGENT_PROFILE
from src.agents.infra_agent import INFRA_AGENT_PROFILE
from src.core.mcp import MCPPool

logger = logging.getLogger(__name__)

# Module-level constant — compiled once, reused on every critic invocation.
_CRITIC_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are the NexusCore Executive Editor.
Your job is to read the conversation and the raw data provided by the specialist agents.
Synthesize their findings into ONE beautifully formatted, cohesive, and concise final answer for the user.

CRITICAL RULES:
- Use Markdown (bolding, bullet points, code blocks if necessary).
- Filter out irrelevant information (e.g., if the user asked for S3 buckets and an agent also returned EC2 data, ignore EC2).
- Be direct, professional, and definitive."""),
    ("placeholder", "{messages}")
])


async def db_agent_node(state: AgentState) -> dict:
    async with MCPPool.acquire() as (_, tool_dict):
        tools = ToolRegistry.resolve(DB_AGENT_PROFILE, tool_dict)
        return await run_tool_loop(DB_AGENT_PROFILE, tools, state)


async def infra_agent_node(state: AgentState) -> dict:
    async with MCPPool.acquire() as (_, tool_dict):
        tools = ToolRegistry.resolve(INFRA_AGENT_PROFILE, tool_dict)
        return await run_tool_loop(INFRA_AGENT_PROFILE, tools, state)


async def critic_node(state: AgentState) -> dict:
    task_id = state.get("task_id", "unknown")
    await broadcast(task_id, "reasoning", "Critic is synthesizing the final answer…")

    response = await (_CRITIC_PROMPT | llm).ainvoke({"messages": state["messages"]})

    logger.info(f"[Critic] Final answer: {response.content}")
    await broadcast(task_id, "final_result", response.content)

    return {
        "completed_steps": ["critic synthesized final answer"],
        "messages": [response],
        "final_report": response.content,
    }
