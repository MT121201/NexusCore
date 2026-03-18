# 🌌 NexusCore: Architecture & Onboarding Guide

Welcome to NexusCore. This document is the technical source of truth for our **Distributed Multi-Agent System (MAS)**. We don't just build "chatbots"; we build resilient, self-healing agentic workflows.

## 1. System Philosophy: "The Durable Brain"
NexusCore solves the **"Fragile AI"** problem (where requests fail due to timeouts or LLM hallucinations).
* **Asynchronous Ingress:** FastAPI handles the handshake; **Temporal** ensures the work actually finishes.
* **Structured Reasoning:** We use **Pydantic Gates** to force the LLM into strict data contracts before any routing happens.
* **Decoupled Tools (MCP):** Agents do not "own" their tools. Tools live in standalone **MCP Servers** (Sidecars) that agents call via a standardized protocol.



---

## 2. Technical Flow: The Request Journey
When a prompt enters the system, it follows a hardened execution path:

1.  **Ingress & Validation:** FastAPI validates the schema. A `TaskID` is returned immediately ($202$ Accepted).
2.  **Temporal Dispatch:** The `AgentOrchestratorWorkflow` is triggered. This manages the lifecycle, state persistence, and **infinite retries** if the AI Worker crashes.
3.  **The Supervisor (The Brain):**
    * Uses `gpt-4o-mini` with a **Structured Output** wrapper.
    * **The Pydantic Gate:** If the LLM returns a confidence score below **0.7**, the system raises a `ValueError`. Temporal catches this and triggers a retry (Self-Correction).
    * **Strict Routing:** We do **not** parse strings. The LLM populates a `next_node` variable in the `AgentState`. The router simply follows the variable.
4.  **The Specialist (The Hands):**
    * The **DB Agent** connects to the **Postgres MCP Server** via `stdio`.
    * It dynamically discovers tools (`list_tables`, `run_query`) via the MCP Protocol.
5.  **Defensive Fallback:** If the LLM makes an impossible routing decision, the **Fallback Node** catches it, logs the event, and ends the workflow gracefully to prevent infinite loops.

---

## 3. Codebase Map (Phase 2: "Structured Brain")

### ⚙️ Core & Configuration
* **`src/models/state.py`**: Our data contracts.
    * `AgentResponse`: The Pydantic model enforcing the **0.7 confidence gate**.
    * `AgentState`: The LangGraph state machine memory.
* **`src/core/config.py`**: Type-safe settings via `pydantic-settings`. **No hardcoded strings allowed.**

### 🧠 The Agents (`src/agents/`)
* **`supervisor.py`**: The heart of the logic. Contains the LangGraph definition and the `execute_agent_graph` Activity.
* **Note on OpenAI:** We use `method="function_calling"` in `with_structured_output` to allow for flexible tool arguments while maintaining strict schema adherence.

### 🛠️ The Tools (`src/mcp/`)
* **`postgres_server.py`**: A standalone **FastMCP** server. It exposes read-only DB tools. It is decoupled from the LLM logic and can be tested in isolation.

---

## 4. Operational Commands

### Local Development
To test the "Hands" (Database Tools) without calling an LLM:
```bash
uv run mcp dev src/mcp/postgres_server.py
```

### Running the Full Stack
1.  **Infrastructure:** `docker compose up -d`
2.  **Temporal:** `temporal server start-dev`
3.  **Worker (The AI):** `uv run python -m src.core.worker`
4.  **API (The Gateway):** `uv run uvicorn src.api.main:app --reload`

---

## 5. Senior Design Patterns Implemented
* **Idempotency:** Every task is tracked by a UUID to prevent duplicate DB writes or server deployments.
* **Subprocess Isolation:** MCP servers run in their own process space. A database driver crash won't kill the main AI Worker.
* **State-Machine Routing:** By using a dedicated `next_node` field in the state, we make the "reasoning" of the AI 100% auditable in the Temporal UI.
