# src/agents/engine.py
import asyncio
import logging
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from src.models.state import AgentState
from src.models.agent import AgentProfile
from src.core.events import publish_agent_event
from src.core.config import settings

logger = logging.getLogger(__name__)

# Module-level LLM singleton — shared safely across concurrent coroutines.
# ChatOpenAI uses an httpx connection pool internally; no per-call overhead.
llm = ChatOpenAI(
    model=settings.openai_model,
    temperature=0,
    api_key=settings.openai_api_key,
    max_retries=settings.openai_max_retries,  # exponential backoff on 429/5xx
)


async def broadcast(task_id: str, event_type: str, message: str) -> None:
    """Publish a real-time event. Awaited directly — Redis publish is non-blocking async."""
    if task_id and task_id != "unknown":
        await publish_agent_event(task_id=task_id, event_type=event_type, message=message)


async def run_tool_loop(
    profile: AgentProfile,
    tools: list[BaseTool],
    state: AgentState,
) -> dict:
    """
    Pure execution engine. Runs the LLM → tool-call → summarize cycle for one
    specialist agent. Tools are pre-resolved from an MCPPool slot by the caller.
    """
    task_id = state.get("task_id", "unknown")
    bound_llm = llm.bind_tools(tools)

    invocation_messages = [SystemMessage(content=profile.system_prompt)] + state["messages"]
    response = await bound_llm.ainvoke(invocation_messages)

    new_steps: list[str] = []
    new_messages = [response]

    if not response.tool_calls:
        logger.info(f"[{profile.name}] Answering directly (no tool calls).")
        new_steps.append(f"{profile.name} answered directly.")
        return {"completed_steps": new_steps, "messages": new_messages}

    logger.info(f"[{profile.name}] Requested {len(response.tool_calls)} tool call(s).")
    invocation_messages.append(response)

    async def _execute(tool: BaseTool, call: dict) -> tuple[ToolMessage, str]:
        await broadcast(task_id, "tool_execution", f"Agent '{profile.name}' running: {tool.name}")
        try:
            res = await tool.ainvoke(call["args"])
            return ToolMessage(content=str(res), tool_call_id=call["id"]), tool.name
        except Exception as e:
            logger.error(f"Tool {tool.name} failed: {e}")
            return ToolMessage(content=f"Error executing tool: {e}", tool_call_id=call["id"]), tool.name

    tasks = []
    for call in response.tool_calls:
        tool = next((t for t in tools if t.name == call["name"]), None)
        if tool:
            tasks.append(_execute(tool, call))
        else:
            logger.warning(f"[{profile.name}] Hallucinated tool '{call['name']}' — skipping.")

    results = await asyncio.gather(*tasks)

    for tool_msg, tool_name in results:
        new_steps.append(f"Executed {profile.name} Tool: {tool_name}")
        new_messages.append(tool_msg)
        invocation_messages.append(tool_msg)

    await broadcast(task_id, "reasoning", f"'{profile.name}' is summarizing results…")
    final_summary = await llm.ainvoke(invocation_messages)
    new_messages.append(final_summary)
    logger.info(f"[{profile.name}] Summary: {final_summary.content}")

    return {"completed_steps": new_steps, "messages": new_messages}
