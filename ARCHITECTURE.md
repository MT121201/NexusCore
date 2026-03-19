# NexusCore: Architecture & Technical Reference

> **Maintenance rule:** Any change to system topology, data flow, or a component's contract must be reflected in this document before the PR is merged.

---

## 1. System Philosophy: "The Durable Brain"

NexusCore solves two production-grade problems simultaneously:

| Problem | Solution |
|---|---|
| Fragile AI (crashes, timeouts, LLM retries) | **Temporal** makes every workflow durable and self-healing |
| Serialized tool execution (stdio bottleneck) | **MCPPool** gives each concurrent task its own subprocess set |

**Core design principles:**
- **Fire-and-forget ingress.** FastAPI returns `202 Accepted` immediately. Temporal owns the lifecycle.
- **Structured outputs as contracts.** Pydantic models enforce LLM response shape before any routing occurs. Invalid shapes are retried by Temporal, not silently swallowed.
- **Subprocess isolation.** MCP tool servers run in separate processes — a driver crash cannot kill the AI worker.
- **Config-driven, not hardcoded.** Every tunable (model name, pool size, concurrency cap, confidence threshold) lives in `config.py` / `.env`.

---

## 2. Request Flow

```
POST /v1/execute
  │
  ▼
FastAPI Gateway          — validates schema, returns task_id (202)
  │  BackgroundTask
  ▼
Temporal Workflow        — AgentOrchestratorWorkflow (durable, retryable)
  │  Activity (5 min timeout, max 3 retries)
  ▼
execute_agent_graph      — runs the LangGraph state machine
  │
  ▼
Supervisor Node          — GPT-4o-mini with structured output (AgentResponse)
  │                        Confidence gate: score < threshold → ValueError → Temporal retry
  │  fan-out (parallel)
  ├──▶ db_agent_node     — acquires MCPPool slot → ToolRegistry.resolve() → run_tool_loop()
  │      └── Postgres MCP subprocess (list_tables, describe_table, run_read_only_query)
  │
  └──▶ infra_agent_node  — acquires MCPPool slot → ToolRegistry.resolve() → run_tool_loop()
         └── AWS MCP subprocess (list_s3_buckets, check_ec2_status)
  │
  ▼  (specialists loop back to supervisor; supervisor routes to critic when done)
Critic Node              — synthesizes findings → final_report → Redis publish
  │
  ▼
Redis Pub/Sub  ──▶  WebSocket (pubsub.listen(), event-driven)  ──▶  Browser UI
```

---

## 3. High-QPS Design

### MCPPool — eliminating the stdio bottleneck

```
Old: 1 MultiServerMCPClient → 2 subprocesses shared by all tasks (serial)

New: MCPPool (pool_size=N)
     ├── Slot 0: client_0 → postgres_mcp PID 101, aws_mcp PID 102
     ├── Slot 1: client_1 → postgres_mcp PID 103, aws_mcp PID 104
     ├── Slot 2: client_2 → postgres_mcp PID 105, aws_mcp PID 106
     └── Slot N: client_N → …
```

Agents acquire a slot via `async with MCPPool.acquire()`. If all slots are busy, the coroutine blocks — natural backpressure, no unbounded subprocess spawning. `MCP_POOL_SIZE` should equal `TEMPORAL_MAX_CONCURRENT_ACTIVITIES` for full throughput.

### Temporal Worker Concurrency

`max_concurrent_activities` and `max_concurrent_workflow_tasks` are set explicitly (from config) so a burst of queued tasks does not overwhelm the MCP pool or exhaust OpenAI rate limits. Tasks beyond the cap queue in Temporal and drain orderly.

### WebSocket streaming (event-driven)

Each WebSocket connection uses `pubsub.listen()` (blocking async generator) instead of polling. This eliminates the O(N × 10 polls/sec) Redis no-op load from the previous `asyncio.sleep(0.1)` loop.

### Confidence gate → Temporal retry

When `supervisor_confidence_threshold` is not met, the supervisor raises `ValueError`. Temporal catches this as a retryable error, backs off, and re-runs the activity on the next attempt — self-healing without any custom retry logic in application code.

---

## 4. Component Reference

### `src/core/config.py` — Settings
All environment variables. **No magic strings anywhere else in the codebase.**

| Setting | Default | Purpose |
|---|---|---|
| `OPENAI_MODEL` | `gpt-4o-mini` | LLM model for all agents |
| `OPENAI_MAX_RETRIES` | `3` | Exponential backoff on 429/5xx |
| `MCP_POOL_SIZE` | `4` | Concurrent MCP subprocess sets |
| `TEMPORAL_MAX_CONCURRENT_ACTIVITIES` | `10` | Worker activity concurrency cap |
| `TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS` | `20` | Worker workflow concurrency cap |
| `SUPERVISOR_CONFIDENCE_THRESHOLD` | `0.7` | Minimum routing confidence |

### `src/core/mcp.py` — MCPPool
Pool of `(MultiServerMCPClient, tool_dict)` pairs. `MCPPool.initialize(pool_size)` is called once at worker startup. `MCPPool.acquire()` is an async context manager that blocks on the internal `asyncio.Queue` until a slot is free.

### `src/agents/tool_registry.py` — ToolRegistry
Stateless. `ToolRegistry.resolve(profile, tool_dict)` returns the subset of tools from a pool slot's `tool_dict` that the profile is permitted to use. Raises `RuntimeError` if any declared tool is missing — fail-fast at startup prevents silent runtime degradation.

### `src/agents/engine.py` — run_tool_loop
Execution engine for all specialists. Accepts pre-resolved tools (from the pool slot). Runs: `LLM.bind_tools → ainvoke → parallel asyncio.gather(tool calls) → summarize`. The LLM singleton (`llm`) is module-level and shared safely across coroutines via httpx's internal connection pool.

### `src/agents/supervisor.py` — supervisor_node
Module-level `_SUPERVISOR_PROMPT` and `_STRUCTURED_LLM` (compiled once). Enforces confidence gate. Valid routes: `db_agent`, `infra_agent`, `critic`. Invalid → `fallback`.

### `src/agents/specialists.py` — specialist & critic nodes
Module-level `_CRITIC_PROMPT`. Each specialist: acquires MCPPool slot → resolves tools → delegates to `run_tool_loop`. Critic: synthesizes via LLM → broadcasts `final_result` → writes `final_report` to state.

### `src/workflows/graph.py` — LangGraph state machine
`_AGENT_GRAPH` is compiled once at module import. Re-used across all concurrent Temporal activities (safe: each `astream()` call owns independent execution state).

### `src/api/main.py` — FastAPI Gateway
`_dispatch_workflow` handles `WorkflowAlreadyStartedError` as an idempotent skip. WebSocket uses `pubsub.listen()` with `await pubsub.aclose()` in `finally`.

---

## 5. Patterns Checklist

When adding or modifying any component, verify:

- [ ] No hardcoded strings — all config in `Settings` + `.env.example`
- [ ] New MCP tools added to `_MCP_SERVER_CONFIG` in `mcp.py`
- [ ] New agents acquire from `MCPPool`, resolve via `ToolRegistry.resolve()`
- [ ] Module-level constants (prompts, compiled LLM chains) — not rebuilt per call
- [ ] Errors are raised (not swallowed) so Temporal retries work correctly
- [ ] `ARCHITECTURE.md` updated
