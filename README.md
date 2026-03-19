# NexusCore

![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.135+-009688.svg?logo=fastapi)
![Temporal](https://img.shields.io/badge/Temporal-Orchestration-111111.svg)
![LangGraph](https://img.shields.io/badge/LangGraph-State_Machine-orange.svg)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg?logo=docker)
![Tests](https://img.shields.io/badge/Tests-50_passing-brightgreen.svg)

**NexusCore** is a production-grade distributed Multi-Agent System (MAS). It treats AI agents as durable microservices — fault-tolerant, horizontally scalable, and capable of surviving worker crashes mid-execution without losing state.

---

## The Problem It Solves

Standard LLM API wrappers fail in production:

| Problem | Consequence |
|---|---|
| HTTP timeout on long reasoning | Request fails, user retries, duplicate work |
| Worker pod OOM or crash | Entire task lost, no recovery |
| Single MCP subprocess for all requests | All tool calls serialize — throughput ceiling at 1 |
| Polling WebSocket loops | O(N × 10 polls/sec) Redis noise per connected client |

NexusCore addresses each of these with explicit architectural decisions.

---

## Architecture

```
POST /v1/execute
  │ returns 202 + task_id immediately
  ▼
FastAPI Gateway ──── BackgroundTask ──── Temporal Workflow (durable, retryable)
                                                │
                                         LangGraph State Machine
                                                │
                                     Supervisor Node (GPT-4o-mini)
                                      structured output · confidence gate
                                                │ fan-out
                                 ┌──────────────┴──────────────┐
                                 ▼                             ▼
                          db_agent_node               infra_agent_node
                          MCPPool.acquire()           MCPPool.acquire()
                               │                           │
                        postgres_mcp PID N         aws_mcp PID M
                        (own subprocess)           (own subprocess)
                                 └──────────────┬──────────────┘
                                                 ▼
                                          Critic Node
                                          synthesizes final answer
                                                 │
                                          Redis Pub/Sub
                                                 │
                                    WebSocket pubsub.listen()
                                                 │
                                           Browser UI
```

### Key Design Decisions

**Durable execution via Temporal** — the API gateway dispatches a workflow and returns `202 Accepted` in milliseconds. If the worker crashes mid-reasoning, Temporal resumes from the last checkpoint on any available worker. No custom retry logic required.

**MCPPool — eliminating the stdio bottleneck** — each concurrent task acquires its own `(MultiServerMCPClient, tool_dict)` pair from an `asyncio.Queue` pool. Pool size N means N tool executions run truly in parallel, each with its own subprocess set. Callers beyond pool capacity block (natural backpressure) rather than spawning unbounded processes.

**Structured output as a contract** — the Supervisor uses `with_structured_output(AgentResponse, method="function_calling")`. The Pydantic model enforces a confidence score ≥ 0.7 at the schema level; a low-confidence response raises `ValueError`, which Temporal treats as a retryable error and backs off with exponential delay.

**Event-driven WebSocket** — `pubsub.listen()` replaces polling loops. Zero Redis no-ops between messages regardless of how many clients are connected.

**Config-driven, not hardcoded** — model name, pool size, concurrency caps, confidence threshold, and all infrastructure URLs are environment variables. No magic strings in application code.

---

## Tech Stack

| Layer | Technology | Role |
|---|---|---|
| Package manager | uv | Deterministic installs from `uv.lock` |
| API | FastAPI + Pydantic | Schema validation, async ingress |
| Orchestration | Temporal.io | Durable, retryable workflow execution |
| AI state machine | LangGraph | Graph-based agent routing |
| LLM | OpenAI GPT-4o-mini | Structured supervisor + specialist reasoning |
| Tool protocol | MCP (stdio) | Decoupled, subprocess-isolated tool servers |
| App database | PostgreSQL 15 + pgvector | Persistent state and vector search |
| Temporal database | PostgreSQL 13 | Temporal's own workflow persistence (md5 auth) |
| Pub/Sub | Redis Stack | Real-time WebSocket event streaming |
| Container | Docker + Compose | Full-stack single-command deployment |

---

## Running with Docker (recommended)

The entire stack — Postgres, Temporal, Redis, API, Worker — starts with one command.

**Prerequisites:** Docker, an OpenAI API key.

```bash
# 1. Clone and enter the repo
git clone https://github.com/MT121201/NexusCore.git && cd NexusCore

# 2. Set your OpenAI key
echo "OPENAI_API_KEY=sk-..." > .env

# 3. Start everything
docker compose up --build -d

# 4. Watch the worker come online
docker compose logs -f nexuscore-worker
```

Expected worker output:
```
MCP pool slot 1/4 ready — 5 tools.
MCP pool slot 2/4 ready — 5 tools.
MCP pool slot 3/4 ready — 5 tools.
MCP pool slot 4/4 ready — 5 tools.
MCP pool ready. Tools available: ['check_ec2_status', 'describe_table', ...]
NexusCore Worker listening on 'nexuscore-task-queue' (max_activities=10, mcp_pool=4)…
```

**Send a task:**
```bash
curl -X POST http://localhost:8000/v1/execute \
     -H "Content-Type: application/json" \
     -d '{"prompt": "List all tables in the database and check EC2 status.", "user_id": "demo"}'
# → {"task_id": "<uuid>", "status": "accepted", "message": "Task queued for orchestration."}
```

**Stream real-time updates:**
Open `http://localhost:8000` in a browser, or connect directly:
```
ws://localhost:8000/ws/task/<task_id>
```

**Check service health:**
```bash
docker compose ps
curl http://localhost:8000/health
```

**Tear down:**
```bash
docker compose down
```

---

## Running Locally (development)

Use this when you want hot-reload and direct log access.

**Prerequisites:** Docker (for infra), uv, Temporal CLI (`brew install temporal` / [install guide](https://docs.temporal.io/cli)).

```bash
uv sync

# Terminal 1 — infrastructure
docker compose up nexuscore-db redis -d

# Terminal 2 — Temporal
temporal server start-dev

# Terminal 3 — AI Worker
uv run python -m src.core.worker

# Terminal 4 — API Gateway
uv run uvicorn src.api.main:app --reload --port 8000
```

---

## Configuration

All settings are in `src/core/config.py` and read from environment variables (`.env` file supported).

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Required |
| `OPENAI_MODEL` | `gpt-4o-mini` | LLM model for all agents |
| `MCP_POOL_SIZE` | `4` | Concurrent MCP subprocess sets |
| `TEMPORAL_MAX_CONCURRENT_ACTIVITIES` | `10` | Worker activity concurrency cap |
| `SUPERVISOR_CONFIDENCE_THRESHOLD` | `0.7` | Minimum routing confidence before Temporal retries |
| `DATABASE_URL` | `postgresql://postgres:postgres@...` | App database |
| `REDIS_URL` | `redis://localhost:6379/0` | Pub/Sub broker |
| `TEMPORAL_HOST` | `localhost:7233` | Temporal frontend address |

For AWS infra tools, set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`.

---

## Testing

The test suite runs without any external services (no Docker, Temporal, Redis, or OpenAI required).

```bash
uv run python -m pytest tests/ -v
# 50 tests, ~2 seconds
```

Coverage:
- `test_config` — settings loading and env override
- `test_models` — Pydantic validation, confidence gate enforcement
- `test_routing` — all LangGraph routing edge cases (fallback, parallel, critic loop)
- `test_tool_registry` — `ToolRegistry.resolve()` correctness and fail-fast behaviour
- `test_mcp_pool` — MCPPool backpressure, slot safety, exception handling, parallel throughput
- `test_api` — FastAPI 202/422/503 paths, idempotency key stability, mocked Temporal

---

## Project Structure

```
src/
├── api/
│   └── main.py              # FastAPI gateway, WebSocket streaming
├── agents/
│   ├── supervisor.py        # LangGraph supervisor node, confidence gate
│   ├── specialists.py       # db_agent, infra_agent, critic nodes
│   ├── engine.py            # run_tool_loop — LLM → tools → summarize
│   ├── tool_registry.py     # Stateless profile → tool resolver
│   ├── db_agent.py          # DB AgentProfile declaration
│   └── infra_agent.py       # Infra AgentProfile declaration
├── workflows/
│   ├── graph.py             # LangGraph compiled graph (module-level singleton)
│   └── orchestrator.py      # Temporal workflow definition
├── core/
│   ├── config.py            # Pydantic settings — single source of truth
│   ├── mcp.py               # MCPPool — async subprocess pool
│   ├── worker.py            # Temporal worker entrypoint
│   └── events.py            # Redis pub/sub client
├── mcp/
│   ├── postgres_server.py   # FastMCP: list_tables, describe_table, run_read_only_query
│   └── aws_server.py        # FastMCP: list_s3_buckets, check_ec2_status
└── models/
    ├── state.py             # AgentState TypedDict, TaskRequest/Response, AgentResponse
    └── agent.py             # AgentProfile base model
tests/                       # 50 unit tests, no external services needed
Dockerfile                   # Multi-stage uv build, non-root runtime user
docker-compose.yml           # Full 6-service stack with health-gated startup order
```

---

## Scaling

**Vertical:** Increase `MCP_POOL_SIZE` and `TEMPORAL_MAX_CONCURRENT_ACTIVITIES` in `.env` to handle more concurrent tasks per worker.

**Horizontal:** Run additional worker containers pointing at the same Temporal task queue. Each worker manages its own MCPPool. Temporal distributes tasks automatically — no coordination code.

```bash
docker compose up --scale nexuscore-worker=3 -d
```
