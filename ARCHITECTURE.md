# 🌌 NexusCore: Architecture & Onboarding Guide

Welcome to the NexusCore project. This document serves as the technical source of truth for our Distributed Multi-Agent System (MAS).

## 1. System Philosophy: "The Durable Brain"
NexusCore is built to solve the "Fragile LLM" problem. Standard AI apps fail when requests time out or the LLM hallucinates a bad format. NexusCore treats AI agents as **durable microservices**:
* **Asynchronous Ingress:** FastAPI accepts the work; Temporal ensures it finishes.
* **Structured Reasoning:** We use **Pydantic Gates** to force the LLM into strict data contracts before any routing occurs.
* **Decoupled Tools (MCP):** Agents do not own the tools (DB/Infra). Tools live in separate **MCP Servers** (Sidecars) that agents call via a standardized protocol.

---

## 2. Technical Flow: The Deep Dive
When a prompt enters the system, it follows this hardened execution path:

1.  **Ingress & Validation:** FastAPI receives the request. Pydantic validates the schema. A `TaskID` is returned immediately.
2.  **Temporal Dispatch:** The `AgentOrchestratorWorkflow` is triggered. This manages the lifecycle, retries, and state persistence of the AI's reasoning.
3.  **The Supervisor (The Brain):** * Uses `gpt-4o-mini` with a **Structured Output** wrapper.
    * **The Pydantic Gate:** If the LLM returns a confidence score below 0.7 or an invalid JSON, the system raises a `ValueError`. Temporal catches this and retries the node.
    * **State-Based Routing:** We do **not** parse strings (e.g., "I will call the DB agent"). The LLM populates a `next_node` variable in the `AgentState`. The router simply looks at this variable.
4.  **The Specialist (The Hands):** * The **DB Agent** initializes a `MultiServerMCPClient`.
    * It executes the **Postgres MCP Server** as a subprocess.
    * The agent dynamically discovers tools (`list_tables`, `run_query`) via the MCP Protocol.
5.  **Defensive Fallback:** If the LLM makes an impossible routing decision, the **Fallback Node** catches the execution, logs the error, and ends the workflow gracefully instead of looping infinitely.

---

## 3. Codebase Map (Phase 2: "Structured Brain" Complete)

### ⚙️ Core & Configuration
* **`.env`**: (Excluded from Git) Stores sensitive keys like `OPENAI_API_KEY`.
* **`src/core/config.py`**: Uses `pydantic-settings` to provide type-safe configuration throughout the app. **No hardcoded strings allowed.**
* **`src/models/state.py`**: Defines our data contracts.
    * `AgentResponse`: The Pydantic model enforcing the 0.7 confidence score gate.
    * `AgentState`: The LangGraph state, now including `next_node` for reliable routing.

### 🧠 The Agents (`src/agents/`)
* **`supervisor.py`**: The heart of the system. Contains the LangGraph definition, the `supervisor_node` (AI Routing), and the `fallback_node` (Safety Net).
* **`mcp_client`**: Integrated directly into specialist nodes to provide dynamic tool-calling.

### 🛠️ The Tools (`src/mcp/`)
* **`postgres_server.py`**: A standalone **FastMCP** server. It exposes safe, read-only database tools to the AI. It can be tested independently of the agents using the `mcp dev` command.

### 🏗️ Orchestration (`src/core/`)
* **`worker.py`**: The Temporal Worker. This is the process that actually executes the LLM logic and MCP tools. It is designed to be horizontally scaled.

---

## 4. Operational Commands

### Development Testing
To test the "Hands" (Database Tools) without running the whole AI:
```bash
uv run mcp dev src/mcp/postgres_server.py
```

### Running the Full Stack
1.  **Infrastructure:** `docker compose up -d`
2.  **Temporal:** `temporal server start-dev`
3.  **Worker (The AI):** `uv run python -m src.core.worker`
4.  **API (The Gateway):** `uv run uvicorn src.api.main:app --reload`

---

## 5. Senior Design Patterns implemented
* **Idempotency:** Every task is tracked by a UUID to prevent duplicate executions.
* **Subprocess Isolation:** MCP servers run in their own process space, preventing a database driver crash from taking down the main AI Worker.
* **Retry-on-Hallucination:** By raising standard Python errors when Pydantic validation fails, we leverage Temporal's retry policies to "self-heal" bad AI responses.
new engineer. Would you like to start **Phase 3**? I can help you build the **AWS MCP Server** to give our `infra_agent` real deployment capabilities, or we can move to **Phase 4** and set up the **Redis/WebSocket** bridge for the UI.