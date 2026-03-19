# src/agents/supervisor.py
import logging
from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate

from src.models.state import AgentState, AgentResponse
from src.agents.engine import llm, broadcast
from src.core.config import settings

logger = logging.getLogger(__name__)

# Module-level constants — compiled once, reused on every supervisor invocation.
_SUPERVISOR_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are the NexusCore Orchestrator. Your job is to route tasks and determine when a workflow is finished.
Analyze the user request and the conversation history.

Available routes for the 'next_agents' list (you may select MULTIPLE to run in parallel):
- 'db_agent': Database queries, schema, tables.
- 'infra_agent': AWS, servers, S3, EC2.
- 'critic': The final QA reviewer and output formatter.

CRITICAL ROUTING RULES:
1. THE FINISH LINE: If the conversation history shows that the specialists have already executed tools and answered the user's request (even if the answer is "no data found"), you MUST route strictly to: ["critic"].
2. NEVER return an empty list []. If you think the job is done, the answer is ["critic"].
3. Do NOT route back to a specialist if they have already provided a final summary or answered the prompt.
4. For dual tasks, return BOTH initially: ["db_agent", "infra_agent"].
5. Do NOT mix 'critic' with other agents in the same list.

Provide your reasoning in 'analysis' and a confidence score."""),
    ("placeholder", "{messages}")
])

_STRUCTURED_LLM = llm.with_structured_output(AgentResponse, method="function_calling")


async def supervisor_node(state: AgentState) -> dict:
    task_id = state.get("task_id", "unknown")
    logger.info(f"Supervisor analyzing task: {task_id}")

    response: AgentResponse = await (_SUPERVISOR_PROMPT | _STRUCTURED_LLM).ainvoke(
        {"messages": state["messages"]}
    )

    logger.info(f"[Supervisor] Analysis: {response.analysis}")
    logger.info(f"[Supervisor] Decision: {response.next_agents} (confidence={response.confidence_score:.2f})")

    # Confidence gate — low-confidence decisions are retried by Temporal.
    if response.confidence_score < settings.supervisor_confidence_threshold:
        raise ValueError(
            f"Supervisor confidence {response.confidence_score:.2f} is below the required "
            f"threshold of {settings.supervisor_confidence_threshold}. Forcing Temporal retry."
        )

    valid_routes = {"db_agent", "infra_agent", "critic"}
    decisions = [a for a in response.next_agents if a in valid_routes]

    if not decisions:
        decisions = ["fallback"]

    # critic must never share a batch with specialists
    if "critic" in decisions and len(decisions) > 1:
        decisions.remove("critic")

    targets_str = ", ".join(decisions)
    await broadcast(task_id, "routing", f"Supervisor delegated to: {targets_str}")

    return {
        "current_agent": "supervisor",
        "next_nodes": decisions,
        "plan": [f"Delegated to {targets_str}"],
        "messages": [AIMessage(content=f"Supervisor Analysis: {response.analysis}")],
    }


async def fallback_node(state: AgentState) -> dict:
    invalid_routes = state.get("next_nodes", ["Unknown"])
    await broadcast(state.get("task_id", "unknown"), "error", "Fallback triggered.")
    return {
        "current_agent": "fallback",
        "next_nodes": ["supervisor"],
        "error_count": state.get("error_count", 0) + 1,
        "messages": [AIMessage(content=f"System Alert: Invalid routes requested ({invalid_routes}).")],
    }
