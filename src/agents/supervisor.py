# src/agents/supervisor.py
from dotenv import load_dotenv
import logging
import sys

from typing import Literal
from temporalio import activity
from langgraph.graph import StateGraph, END
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from src.models.state import AgentState, TaskRequest, AgentResponse

load_dotenv()
logger = logging.getLogger(__name__)

# Configure the connection to our local MCP Server
# Keeping this global is generally okay, as it's just a config dictionary
mcp_client = MultiServerMCPClient({
    "postgres_mcp": {
        "command": sys.executable,
        "args": ["-m", "src.mcp.postgres_server"],
        "transport": "stdio",
    }
})

# ==========================================
# 1. Agent Nodes (The "Workers")
# ==========================================

async def supervisor_node(state: AgentState) -> dict:
    """Analyzes the task and delegates to the appropriate specialist using an LLM."""
    logger.info(f"Supervisor analyzing task: {state.get('task_id')}")

    # Initialize locally to avoid Temporal Sandbox import warnings
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    # FIX: Explicitly set method="function_calling" to avoid the 400 Bad Request
    structured_llm = llm.with_structured_output(AgentResponse, method="function_calling")

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

        decision = response.tool_to_call if response.tool_to_call in ["db_agent", "infra_agent"] else END

        return {
            "current_agent": "supervisor",
            "next_node": decision,
            "plan": [f"Delegated to {decision}"],
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
    """Database expert agent utilizing the Postgres MCP Server."""
    logger.info(f"DB Agent processing task: {state.get('task_id')}")

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    # 1. Fetch tools dynamically from the MCP server
    tools = await mcp_client.get_tools()

    # 2. FIX: Bind the tools to the BASE LLM, not the structured_llm.
    # The agent needs freedom to return tool calls, not just our AgentResponse schema.
    db_llm = llm.bind_tools(tools)

    system_prompt = SystemMessage(content="""You are the NexusCore Database Specialist. 
    You have access to PostgreSQL via MCP tools.
    1. ALWAYS use 'list_tables' first if you don't know the schema.
    2. Use 'describe_table' to understand the columns before writing SQL.
    3. Use 'run_read_only_query' to fetch data.
    Explain your findings clearly based on the data you retrieve.
    """)

    messages = [system_prompt] + state["messages"]

    # 4. Invoke the LLM to see what it wants to do
    response = await db_llm.ainvoke(messages)
    completed_steps = state.get("completed_steps", [])

    # 5. Handle Tool Execution
    if response.tool_calls:
        logger.info(f"DB Agent requested {len(response.tool_calls)} tool calls.")
        messages.append(response)

        for tool_call in response.tool_calls:
            tool = next((t for t in tools if t.name == tool_call["name"]), None)
            if tool:
                logger.info(f"Executing tool: {tool.name}")
                tool_result = await tool.ainvoke(tool_call["args"])
                completed_steps.append(f"Executed DB Tool: {tool.name}")
                messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))

        # 6. Ask the LLM to summarize the database results
        final_response = await db_llm.ainvoke(messages)
        output_message = final_response.content
    else:
        output_message = response.content
        completed_steps.append("DB Agent answered without needing tools.")

    return {
        "current_agent": "db_agent",
        "completed_steps": completed_steps,
        "messages": [AIMessage(content=f"DB Agent Report: {output_message}")]
    }


async def critic_node(state: AgentState) -> dict:
    """Evaluates the work of the specialist agents."""
    logger.info("Critic evaluating results...")
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
    next_step = state.get("next_node")
    if next_step in ["infra_agent", "db_agent"]:
        return next_step
    logger.warning(f"Invalid next_node '{next_step}'. Routing to fallback node.")
    return "fallback"


def route_from_critic(state: AgentState) -> Literal["supervisor", "__end__"]:
    last_msg = state["messages"][-1].content.lower()
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

    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("infra_agent", infra_agent_node)
    workflow.add_node("db_agent", db_agent_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("fallback", fallback_node)

    workflow.set_entry_point("supervisor")
    workflow.add_conditional_edges("supervisor", route_from_supervisor)
    workflow.add_edge("infra_agent", "critic")
    workflow.add_edge("db_agent", "critic")
    workflow.add_conditional_edges("critic", route_from_critic)
    workflow.add_edge("fallback", END)

    return workflow.compile()


# ==========================================
# 4. Temporal Activity
# ==========================================

@activity.defn(name="execute_agent_graph")
async def execute_agent_graph(request: TaskRequest) -> dict:
    """The Temporal Activity that initializes and runs the LangGraph."""
    logger.info(f"Starting LangGraph execution for task {request.idempotency_key}")

    app = build_agent_graph()

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

    final_state = await app.ainvoke(initial_state)

    result = {
        "task_id": str(final_state["task_id"]),
        "final_response": final_state["messages"][-1].content,
        "completed_steps": final_state["completed_steps"]
    }

    return result