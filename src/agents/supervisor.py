# src/agents/supervisor.py
import os
from dotenv import load_dotenv

import logging
from typing import Literal
from temporalio import activity
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from src.models.state import AgentState, TaskRequest, AgentResponse
from src.core.config import Settings

load_dotenv()
logger = logging.getLogger(__name__)


# Initialize the LLM with structured output mapping to our Pydantic Gate
# We use temperature=0 because we want deterministic, logical routing, not creativity.
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
structured_llm = llm.with_structured_output(AgentResponse)


# ==========================================
# 1. Agent Nodes (The "Workers")
# ==========================================

async def supervisor_node(state: AgentState) -> dict:
    """Analyzes the task and delegates to the appropriate specialist using an LLM."""
    logger.info(f"Supervisor analyzing task: {state.get('task_id')}")

    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are the NexusCore Supervisor Agent. Your job is to analyze the user's request and delegate it to the correct specialist.

        Available specialists (use exactly these names for 'tool_to_call'):
        - 'db_agent': For anything related to databases, logs, queries, or user tables.
        - 'infra_agent': For anything related to AWS, servers, infrastructure, or deployments.

        Provide your step-by-step reasoning in the 'analysis' field.
        You must provide a confidence score. If you are unsure, score it below 0.7.
        """),
        ("placeholder", "{messages}")
    ])

    chain = prompt | structured_llm

    try:
        response: AgentResponse = await chain.ainvoke({"messages": state["messages"]})
        logger.info(f"Supervisor Decision: {response.tool_to_call} (Confidence: {response.confidence_score})")

        # Explicit mapping of the tool_to_call to our node names
        decision = response.tool_to_call if response.tool_to_call in ["db_agent", "infra_agent"] else END

        return {
            "current_agent": "supervisor",
            "next_node": decision,
            "plan": [f"Delegated to {decision}"],
            # Notice the message is now clean and only contains the AI's reasoning!
            "messages": [AIMessage(content=f"Analysis: {response.analysis}")]
        }
    except Exception as e:
        logger.error(f"Supervisor routing failed validation: {e}")
        raise ValueError(f"Supervisor failed to generate valid routing: {e}")


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


async def fallback_node(state: AgentState) -> dict:
    """Catches invalid routing decisions and handles them gracefully."""
    invalid_route = state.get("next_node", "Unknown")
    logger.warning(f"Fallback node triggered! Supervisor attempted to route to: {invalid_route}")

    return {
        "current_agent": "fallback",
        "error_count": state.get("error_count", 0) + 1,
        "completed_steps": state.get("completed_steps", []) + ["Fallback Protocol Activated"],
        "messages": [AIMessage(
            content=f"System Alert: I could not determine how to handle your request. (Attempted invalid route: {invalid_route}). Please try rephrasing.")]
    }


# ==========================================
# 2. Routing Logic (Conditional Edges)
# ==========================================

def route_from_supervisor(state: AgentState) -> Literal["infra_agent", "db_agent", "fallback"]:
    """Determines where to send the task using the strictly typed next_node field."""
    next_step = state.get("next_node")

    if next_step in ["infra_agent", "db_agent"]:
        return next_step

    # If the LLM returned None, or made up a node name, send it to the fallback catcher
    logger.warning(f"Invalid next_node '{next_step}'. Routing to fallback node.")
    return "fallback"


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
    """Constructs the LangGraph state machine."""
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("infra_agent", infra_agent_node)
    workflow.add_node("db_agent", db_agent_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("fallback", fallback_node)  # <--- Register the new fallback node

    # Add edges
    workflow.set_entry_point("supervisor")

    workflow.add_conditional_edges("supervisor", route_from_supervisor)

    workflow.add_edge("infra_agent", "critic")
    workflow.add_edge("db_agent", "critic")

    workflow.add_conditional_edges("critic", route_from_critic)

    # The fallback node simply ends the workflow gracefully after reporting the error
    workflow.add_edge("fallback", END)

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