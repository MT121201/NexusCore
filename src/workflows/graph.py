# src/workflows/graph.py
import logging
from typing import Literal
from temporalio import activity
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage

from src.models.state import AgentState, TaskRequest
from src.agents import supervisor, specialists

logger = logging.getLogger(__name__)


# --- Routing Logic ---

def route_from_supervisor(state: AgentState) -> list[str]:
    targets = state.get("next_nodes", [])
    if not targets:
        return ["fallback"]
    valid = [t for t in targets if t in {"infra_agent", "db_agent", "critic"}]
    return valid if valid else ["fallback"]


def route_from_critic(state: AgentState) -> Literal["supervisor", "__end__"]:
    next_targets = state.get("next_nodes", [])
    if "supervisor" in next_targets:
        return "supervisor"
    return END


# --- Graph compiled once at import time ---
# LangGraph compiled graphs are stateless: each astream() call creates its own
# execution context, so sharing this object across concurrent activities is safe.

def _build_agent_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("supervisor", supervisor.supervisor_node)
    workflow.add_node("infra_agent", specialists.infra_agent_node)
    workflow.add_node("db_agent", specialists.db_agent_node)
    workflow.add_node("critic", specialists.critic_node)
    workflow.add_node("fallback", supervisor.fallback_node)

    workflow.set_entry_point("supervisor")

    workflow.add_conditional_edges("supervisor", route_from_supervisor)
    workflow.add_edge("infra_agent", "supervisor")
    workflow.add_edge("db_agent", "supervisor")
    workflow.add_conditional_edges("critic", route_from_critic)
    workflow.add_edge("fallback", END)

    return workflow.compile()


_AGENT_GRAPH = _build_agent_graph()


# --- Temporal Activity ---

@activity.defn(name="execute_agent_graph")
async def execute_agent_graph(request: TaskRequest) -> dict:
    task_id = str(request.idempotency_key)

    initial_state: AgentState = {
        "messages": [HumanMessage(content=request.prompt)],
        "task_id": task_id,
        "idempotency_key": task_id,
        "current_agent": "system",
        "next_nodes": [],
        "plan": [],
        "completed_steps": [],
        "final_report": None,
        "error_count": 0,
    }

    logger.info(f"Starting graph execution for task: {task_id}")
    final_state = dict(initial_state)

    try:
        async for output in _AGENT_GRAPH.astream(initial_state):
            for node_name, state_update in output.items():
                logger.info(f"[Graph] Finished node: {node_name}")
                final_state.update(state_update)

        return {
            "final_response": final_state.get("final_report", "No report generated."),
            "steps_taken": final_state.get("completed_steps", []),
        }

    except Exception as e:
        logger.error(f"Graph execution crashed for task {task_id}: {e}", exc_info=True)
        raise
