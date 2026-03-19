# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Setup
```bash
uv sync                    # Install dependencies
docker compose up -d       # Start PostgreSQL (pgvector) and Redis
```

### Running the system (4 terminals)
```bash
temporal server start-dev                              # Terminal 1: Temporal orchestrator (port 7233)
uv run python -m src.core.worker                      # Terminal 2: AI worker (Temporal activity runner)
uv run uvicorn src.api.main:app --reload --port 8000  # Terminal 3: FastAPI gateway
# Terminal 4: open http://localhost:8000/index.html   # Browser UI
```

### MCP server testing
```bash
uv run mcp dev src/mcp/postgres_server.py  # Test Postgres MCP in isolation
uv run mcp dev src/mcp/aws_server.py       # Test AWS MCP in isolation
```

### Testing & linting
```bash
pytest tests/
pytest tests/ --cov=src
uv run ruff check src/
uv run black src/
```

## Engineering Standards

- **Any architecture change must update `ARCHITECTURE.md`** — this is the technical source of truth. Code and docs must stay in sync.
- **Senior design patterns over prototype shortcuts.** Production correctness (idempotency, backpressure, connection pooling, fail-fast) takes priority over cleverness or brevity.
- **Production over prototype.** No in-process mocks, no hardcoded strings outside `config.py`/`.env.example`, no silently swallowed errors.

## Architecture

NexusCore is a **durable AI orchestration platform** that decouples HTTP ingress from fault-tolerant agent execution. The core insight: FastAPI returns `202 Accepted` immediately; Temporal ensures the agent graph runs to completion even if the worker crashes.

### Request flow

```
POST /v1/execute
  → FastAPI (returns task_id immediately)
  → Temporal workflow (AgentOrchestratorWorkflow)
  → Temporal activity (execute_agent_graph)
  → LangGraph state machine
    → Supervisor node (GPT-4o-mini routes to specialists)
    → Parallel specialist nodes (db_agent, infra_agent)
    → Critic node (synthesizes final answer)
  → Redis Pub/Sub → WebSocket → Browser
```

### Key components

**`src/api/main.py`** — FastAPI gateway. Exposes `POST /v1/execute`, `GET /health`, and `WebSocket /ws/task/{task_id}`. The WebSocket subscribes to Redis and streams real-time updates to the browser.

**`src/workflows/graph.py`** — LangGraph state machine. Graph topology: `supervisor → [db_agent | infra_agent | critic | fallback]`. Specialist nodes loop back to supervisor; critic routes to END when satisfied.

**`src/agents/supervisor.py`** — Entry point node. Uses structured LLM output (`AgentResponse`) to decide which specialists to invoke in parallel. Confidence must be ≥ 0.7 or falls back.

**`src/agents/specialists.py`** — `db_agent_node`, `infra_agent_node`, and `critic_node`. Specialists call tools via `run_tool_loop` (in `engine.py`). The critic synthesizes all findings and broadcasts to Redis.

**`src/agents/tool_registry.py`** — Stateless resolver. `ToolRegistry.resolve(profile, tool_dict)` maps an `AgentProfile.allowed_tools` list onto concrete `BaseTool` instances from the caller's MCPPool slot. Raises `RuntimeError` on any missing tool (fail-fast).

**`src/agents/db_agent.py` / `infra_agent.py`** — Declarative `AgentProfile` objects listing `name`, `model`, `system_prompt`, and `allowed_tools`. No logic here.

**`src/mcp/postgres_server.py`** — MCP subprocess: `list_tables`, `describe_table`, `run_read_only_query` (SELECT only, LIMIT 50).

**`src/mcp/aws_server.py`** — MCP subprocess: `list_s3_buckets`, `check_ec2_status`.

**`src/core/mcp.py`** — `MCPPool` manages N independent sets of MCP subprocesses (one pair per slot). Agents acquire a slot via `async with MCPPool.acquire()`, run their tools, then release it — preventing stdio serialization under concurrent load.

**`src/core/worker.py`** — Temporal worker daemon. Connects MCPManager, initializes ToolRegistry, then starts the Temporal worker loop.

**`src/core/events.py`** — Async Redis client. Publishes structured events to `task_updates:{task_id}` channel.

**`src/models/state.py`** — `AgentState` TypedDict (LangGraph state). Key fields: `messages` (accumulates with `operator.add`), `next_nodes` (routing), `error_count` (loop guard), `final_report`.

### Configuration (`.env`)
```
OPENAI_API_KEY=...
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/nexuscore
REDIS_URL=redis://localhost:6379/0
TEMPORAL_HOST=localhost:7233
TEMPORAL_TASK_QUEUE=nexuscore-task-queue
```

### Adding a new specialist agent
1. Create an `AgentProfile` in `src/agents/<name>_agent.py` with `allowed_tools`
2. Add an MCP tool server in `src/mcp/<name>_server.py`
3. Add the server entry to `_MCP_SERVER_CONFIG` in `src/core/mcp.py`
4. Add the node function in `src/agents/specialists.py` — acquire pool slot, resolve tools, call `run_tool_loop`
5. Wire the node into the LangGraph in `src/workflows/graph.py`
6. Add the agent name to supervisor's routing options in `src/agents/supervisor.py`
7. **Update `ARCHITECTURE.md`** to document the new agent, its tools, and routing rules
