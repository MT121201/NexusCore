# NexusCore: Architecture & Onboarding Guide

Welcome to the NexusCore project! This document will get you up to speed on our Distributed Multi-Agent System (MAS) in about 5 minutes.

## 1. System Philosophy
NexusCore is not a standard synchronous API. Because LLM agents can take minutes to reason, plan, and execute, we use a **Fire-and-Forget + Asynchronous Worker** architecture. 
* **FastAPI** handles the immediate web traffic.
* **Temporal** acts as our invincible orchestrator, ensuring tasks never drop.
* **LangGraph** acts as the "Brain", routing tasks between specialized AI agents.

## 2. The End-to-End Request Flow
When a user asks the AI to do something, here is the exact journey of that request:

1. **Ingress:** User sends a POST request to `/v1/execute` on the **FastAPI Gateway**.
2. **Acceptance:** The Gateway validates the payload via Pydantic, generates a unique `idempotency_key` (Task ID), and returns a `202 Accepted` to the user instantly.
3. **Handoff:** In the background, FastAPI sends a signal to the **Temporal Server** to start the `AgentOrchestratorWorkflow`.
4. **Orchestration:** A **Temporal Worker** (running on a separate pod/process) picks up the workflow. The workflow guarantees execution (if the pod crashes, Temporal moves the task to a new pod).
5. **The Brain (LangGraph):** The workflow triggers the `execute_agent_graph` Activity. This boots up our LangGraph state machine:
   * **Supervisor Agent:** Reads the prompt and decides which specialist to route to.
   * **Specialist Agents (DB/Infra):** Execute the specific tasks using MCP tools.
   * **Critic Agent:** Reviews the output for hallucinations. Loops back if it fails.
6. **Completion:** The graph finishes, returning the final data to the Temporal Workflow, which marks the overarching task as "Complete". *(Note: Future phases will broadcast this completion to a WebSocket UI via Redis).*

---

## 3. Current Codebase Map (Phase 1 Complete)

We use `uv` for dependency management. Here is what we have built so far and where to find it:

### Infrastructure
* `docker-compose.yml`: Spins up PostgreSQL (with pgvector) and Redis Stack for local development.

### Application Code (`/src`)
* **`models/state.py`**: The central source of truth for our data shapes. Contains Pydantic models for API requests (`TaskRequest`) and the TypedDict for the LangGraph memory (`AgentState`).
* **`api/main.py`**: The FastAPI application. Contains the lifespan manager that connects to Temporal on startup, and the `/v1/execute` endpoint.
* **`workflows/orchestrator.py`**: Contains the Temporal Workflow (`AgentOrchestratorWorkflow`). This is the resilient manager that tells the AI to start and sets timeouts/retry policies.
* **`agents/supervisor.py`**: The LangGraph state machine. It contains the individual node functions (Supervisor, DB Agent, Critic) and the routing logic. It also wraps the graph execution in a Temporal Activity (`execute_agent_graph`).
* **`core/worker.py`**: The daemon script. Running this file boots up a Temporal Worker that listens to the `nexuscore-task-queue` and actually executes the workflows/activities.

### How to Run Locally
1. Start infrastructure: `docker compose up -d`
2. Start Temporal Dev Server: `temporal server start-dev`
3. Start the AI Worker: `uv run python -m src.core.worker`
4. Start the API Gateway: `uv run uvicorn src.api.main:app --reload --port 8000`