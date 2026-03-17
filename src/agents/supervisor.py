# src/agents/supervisor.py
import logging
from typing import Literal
from temporalio import activity
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage

from src.models.state import AgentState, TaskRequest

logger = logging.getLogger(__name__)


# ==========================================
# 1. Agent Nodes (The "Workers")
# ==========================================

async def supervisor_node(state: AgentState) -> dict:
    """Analyzes the task and delegates to the appropriate specialist."""
    logger.info(f"Supervisor analyzing task: {state.get('task_id')}")

    # TODO: In the next phase, we will inject a real LLM here (e.g., ChatOpenAI/ChatAnthropic)
    # to dynamically generate the plan and choose the next node.
    # For now, we mock the routing logic to pass the structure check.

    last_message = state["messages"][-1].content if state.get("messages") else ""

    # Mock routing logic based on prompt keywords
    next_step = "db_agent" if "database" in last_message.lower() else "infra_agent"

    return {
        "current_agent": "supervisor",
        "plan": [f"Route to {next_step}", "Validate results"],
        "messages": [AIMessage(content=f"I have decided to route this to the {next_step}.")]
    }


async def infra_agent_node(state: AgentState) -> dict:
    """Interacts with the AWS MCP server to manage infrastructure."""
    logger.info("Infra Agent executing...")
    return {
        "current_agent": "infra_agent",
        "completed_steps": state.get("completed_steps", []) + ["Checked AWS Infrastructure"],
        "messages": [AIMessage(content="Infra Agent: AWS servers are healthy.")]
    }


async def db_agent_node(state: AgentState) -> dict:
    """Interacts with the Postgres MCP server to query data."""
    logger.info("DB Agent executing...")
    return {
        "current_agent": "db_agent",
        "completed_steps": state.get("completed_steps", []) + ["Queried Database Logs"],
        "messages": [AIMessage(content="DB Agent: No anomalies found in DB logs.")]
    }


async def critic_node(state: AgentState) -> dict:
    """Evaluates the work of the specialist agents."""
    logger.info("Critic evaluating results...")
    # Mocking a successful validation WITHOUT triggering the error keyword!
    return {
        "current_agent": "critic",
        "messages": [AIMessage(content="Critic: The results are verified, accurate, and ready for the user.")]
    }




# ==========================================
# 2. Routing Logic (Conditional Edges)
# ==========================================

def route_from_supervisor(state: AgentState) -> Literal["infra_agent", "db_agent"]:
    """Determines where the supervisor should send the task."""
    last_msg = state["messages"][-1].content
    if "db_agent" in last_msg:
        return "db_agent"
    return "infra_agent"



def route_from_critic(state: AgentState) -> Literal["supervisor", "__end__"]:
    """Determines if the graph should loop back or finish."""
    last_msg = state["messages"][-1].content.lower()

    # Check for actual failure words, avoiding our success message
    if "error" in last_msg or "fail" in last_msg or state.get("error_count", 0) > 0:
        logger.info("Critic rejected the result. Routing back to Supervisor.")
        return "supervisor"

    logger.info("Critic approved the result. Finishing workflow.")
    return END


# ==========================================
# 3. Graph Compilation
# ==========================================

def build_agent_graph():
    """Constructs the LangGraph state machine based on flow.md."""
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("infra_agent", infra_agent_node)
    workflow.add_node("db_agent", db_agent_node)
    workflow.add_node("critic", critic_node)

    # Add edges
    workflow.set_entry_point("supervisor")

    workflow.add_conditional_edges("supervisor", route_from_supervisor)

    workflow.add_edge("infra_agent", "critic")
    workflow.add_edge("db_agent", "critic")

    workflow.add_conditional_edges("critic", route_from_critic)

    return workflow.compile()


# ==========================================
# 4. Temporal Activity
# ==========================================

@activity.defn(name="execute_agent_graph")
async def execute_agent_graph(request: TaskRequest) -> dict:
    """
    The Temporal Activity that initializes and runs the LangGraph.
    """
    logger.info(f"Starting LangGraph execution for task {request.idempotency_key}")

    # Compile the graph
    app = build_agent_graph()

    # Initialize state
    initial_state = {
        "messages": [HumanMessage(content=request.prompt)],
        "task_id": request.idempotency_key,
        "idempotency_key": request.idempotency_key,
        "current_agent": "system",
        "plan": [],
        "completed_steps": [],
        "final_report": None,
        "error_count": 0
    }

    # Run the graph asynchronously
    final_state = await app.ainvoke(initial_state)

    # Extract the final string output for Temporal to store
    # (Temporal prefers standard dicts/strings over complex LangChain message objects in its history)
    result = {
        "task_id": str(final_state["task_id"]),
        "final_response": final_state["messages"][-1].content,
        "completed_steps": final_state["completed_steps"]
    }

    return result