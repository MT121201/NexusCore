# NexusCore Developer Guide: Adding Tools and Agents

This document is the practical reference for extending NexusCore with new capabilities. It covers two extension paths: adding a tool to an existing agent, and adding an entirely new specialist agent.

---

## Design Principles (read this first)

Understanding these constraints prevents the most common mistakes.

**1. Agents never import SDKs directly.**
No `boto3`, `asyncpg`, or other SDK calls inside agent code. All I/O goes through an MCP server subprocess. This keeps the AI reasoning layer stateless and the tools independently testable.

**2. AgentProfile is the permission boundary.**
An agent can only call tools listed in its `allowed_tools`. `ToolRegistry.resolve()` enforces this and raises `RuntimeError` if a declared tool is missing from the pool — fail-fast at startup, not at runtime.

**3. Tools are resolved from the pool, not from a global cache.**
Each tool call acquires a `(client, tool_dict)` slot from `MCPPool`. The tool objects are bound to that client's subprocess session. Do not store tool references outside the `async with MCPPool.acquire()` block.

**4. Module-level constants, not per-call construction.**
Prompt templates, bound LLM chains, and compiled graphs are built once at import time. Never construct them inside node functions.

**5. All config in `Settings`, all settings in `.env.example`.**
No hardcoded strings in application code. If a value might differ between environments, it belongs in `src/core/config.py`.

---

## Path A: Add a tool to an existing agent

Use this when you want to give `db_agent` or `infra_agent` a new capability.

### Step 1 — Implement the tool in the MCP server

Open the relevant MCP server and add a `@mcp.tool()` function.

```python
# src/mcp/postgres_server.py

@mcp.tool()
async def count_rows(table_name: str) -> str:
    """Returns the total row count for a given table."""
    conn = await get_db_connection()
    try:
        row = await conn.fetchrow(f"SELECT COUNT(*) AS n FROM {table_name}")
        return str(row["n"])
    finally:
        await conn.close()
```

Rules for MCP tool functions:
- Must be `async`
- Return type must be serialisable (str, dict, list — MCP serialises over stdio)
- Validate inputs defensively; the LLM may pass unexpected values
- Keep tools narrowly scoped — one tool, one job

### Step 2 — Add the tool name to the AgentProfile

```python
# src/agents/db_agent.py

DB_AGENT_PROFILE = AgentProfile(
    name="db_agent",
    system_prompt="""...""",
    allowed_tools=["list_tables", "describe_table", "run_read_only_query", "count_rows"]  # added
)
```

### Step 3 — Update the system prompt (if needed)

If the tool requires guidance on when to use it, add a line to the agent's `system_prompt`:

```python
system_prompt="""You are the NexusCore Database Specialist.
1. ALWAYS use 'list_tables' first if schema is unknown.
2. Use 'describe_table' before writing SQL.
3. Use 'run_read_only_query' to fetch data.
4. Use 'count_rows' when the user asks how many records exist in a table.
RULE: If anything relates to AWS, ignore it.""",
```

### Step 4 — Restart the worker

`MCPPool.initialize()` calls `client.get_tools()` at startup. The new tool is discovered automatically — no code changes to the pool or registry.

```bash
docker compose restart nexuscore-worker
# or locally:
uv run python -m src.core.worker
```

### Verify

```bash
docker compose logs nexuscore-worker | grep "Tools available"
# Should show: [..., 'count_rows', ...]
```

**That's it. No graph changes, no routing changes.**

---

## Path B: Add a completely new specialist agent

Use this when the new capability belongs to a separate domain (e.g., GitHub, Slack, Kubernetes) and warrants its own MCP server and routing path.

This involves 7 files. Work through them in order — each step compiles independently so you can test as you go.

---

### Step 1 — Create the MCP server

```python
# src/mcp/github_server.py

import logging
from mcp.server.fastmcp import FastMCP
import httpx

logger = logging.getLogger(__name__)
mcp = FastMCP("GitHub-MCP-Sidecar")

GITHUB_TOKEN = ""  # inject via env in production


@mcp.tool()
async def list_open_prs(repo: str) -> str:
    """Lists open pull requests for a GitHub repo (format: 'owner/repo')."""
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://api.github.com/repos/{repo}/pulls?state=open", headers=headers)
        prs = resp.json()
        return "\n".join(f"#{p['number']} {p['title']}" for p in prs[:10])


@mcp.tool()
async def get_pr_diff(repo: str, pr_number: int) -> str:
    """Returns the diff for a specific pull request."""
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.diff"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}", headers=headers
        )
        return resp.text[:4000]  # truncate for LLM context


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

---

### Step 2 — Register the server in MCPPool

```python
# src/core/mcp.py  —  add to _MCP_SERVER_CONFIG

_MCP_SERVER_CONFIG = {
    "postgres_mcp": { ... },
    "aws_mcp": { ... },
    "github_mcp": {                              # ← add this
        "command": sys.executable,
        "args": ["-m", "src.mcp.github_server"],
        "transport": "stdio",
    },
}
```

Every pool slot now spawns a `github_mcp` subprocess alongside the existing ones. All slots stay symmetrical.

---

### Step 3 — Create the AgentProfile

```python
# src/agents/github_agent.py

from src.models.agent import AgentProfile

GITHUB_AGENT_PROFILE = AgentProfile(
    name="github_agent",
    system_prompt="""You are the NexusCore GitHub Specialist.
You have access to GitHub repositories via MCP tools.
1. Use 'list_open_prs' to find open pull requests for a repo.
2. Use 'get_pr_diff' to inspect the code changes in a specific PR.
3. Do not attempt database queries or AWS operations — that is not your domain.""",
    allowed_tools=["list_open_prs", "get_pr_diff"],
)
```

---

### Step 4 — Add the specialist node

```python
# src/agents/specialists.py  —  add alongside existing nodes

from src.agents.github_agent import GITHUB_AGENT_PROFILE

async def github_agent_node(state: AgentState) -> dict:
    async with MCPPool.acquire() as (_, tool_dict):
        tools = ToolRegistry.resolve(GITHUB_AGENT_PROFILE, tool_dict)
        return await run_tool_loop(GITHUB_AGENT_PROFILE, tools, state)
```

No other logic needed. `run_tool_loop` handles the full LLM → tool-call → summarize cycle.

---

### Step 5 — Wire into the LangGraph

```python
# src/workflows/graph.py

from src.agents.specialists import db_agent_node, infra_agent_node, critic_node, github_agent_node

def _build_agent_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("supervisor", supervisor.supervisor_node)
    workflow.add_node("db_agent",     specialists.db_agent_node)
    workflow.add_node("infra_agent",  specialists.infra_agent_node)
    workflow.add_node("github_agent", specialists.github_agent_node)   # ← add
    workflow.add_node("critic",       specialists.critic_node)
    workflow.add_node("fallback",     supervisor.fallback_node)

    workflow.set_entry_point("supervisor")

    workflow.add_conditional_edges("supervisor", route_from_supervisor)
    workflow.add_edge("db_agent",     "supervisor")
    workflow.add_edge("infra_agent",  "supervisor")
    workflow.add_edge("github_agent", "supervisor")                    # ← add
    workflow.add_conditional_edges("critic", route_from_critic)
    workflow.add_edge("fallback", END)

    return workflow.compile()
```

Also update the routing validator to accept the new agent name:

```python
# src/workflows/graph.py

def route_from_supervisor(state: AgentState) -> list[str]:
    targets = state.get("next_nodes", [])
    if not targets:
        return ["fallback"]
    valid = [t for t in targets if t in {"infra_agent", "db_agent", "github_agent", "critic"}]  # ← add
    return valid if valid else ["fallback"]
```

---

### Step 6 — Update the Supervisor

Two changes: add the route description to the system prompt, and update the valid routes set.

```python
# src/agents/supervisor.py

_SUPERVISOR_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are the NexusCore Orchestrator. ...

Available routes for the 'next_agents' list (you may select MULTIPLE to run in parallel):
- 'db_agent':     Database queries, schema, tables.
- 'infra_agent':  AWS, servers, S3, EC2.
- 'github_agent': GitHub pull requests, diffs, code review.    ← add
- 'critic':       The final QA reviewer and output formatter.

..."""),
    ("placeholder", "{messages}")
])

# further down in supervisor_node():
valid_routes = {"db_agent", "infra_agent", "github_agent", "critic"}   # ← add
```

---

### Step 7 — Update ARCHITECTURE.md

Add the new agent to the component reference table and the request flow diagram. This is mandatory per the engineering standards rule.

---

### Checklist

```
[ ] src/mcp/<name>_server.py         — FastMCP tools, ends with mcp.run(transport="stdio")
[ ] src/core/mcp.py                  — entry added to _MCP_SERVER_CONFIG
[ ] src/agents/<name>_agent.py       — AgentProfile with allowed_tools
[ ] src/agents/specialists.py        — node function using MCPPool.acquire()
[ ] src/workflows/graph.py           — node registered, edge added, valid set updated
[ ] src/agents/supervisor.py         — route description in prompt, valid_routes set updated
[ ] ARCHITECTURE.md                  — component reference updated
[ ] tests/test_routing.py            — add routing test for the new agent name
[ ] tests/test_tool_registry.py      — add resolve() test for the new profile
```

---

## Testing the extension without Docker

`ToolRegistry.resolve()` and routing are both pure functions — test them immediately without starting any services.

```python
# tests/test_tool_registry.py

def test_github_agent_resolves_tools():
    tool_dict = {
        "list_open_prs": _make_tool("list_open_prs"),
        "get_pr_diff":   _make_tool("get_pr_diff"),
    }
    result = ToolRegistry.resolve(GITHUB_AGENT_PROFILE, tool_dict)
    assert [t.name for t in result] == ["list_open_prs", "get_pr_diff"]
```

```python
# tests/test_routing.py

def test_routes_to_github_agent():
    state = {"next_nodes": ["github_agent"]}
    assert route_from_supervisor(state) == ["github_agent"]

def test_routes_db_and_github_in_parallel():
    state = {"next_nodes": ["db_agent", "github_agent"]}
    result = route_from_supervisor(state)
    assert "db_agent" in result and "github_agent" in result
```

Test the MCP server in isolation before wiring it into the pool:

```bash
uv run mcp dev src/mcp/github_server.py
```

---

## Common Mistakes

| Mistake | What happens | Fix |
|---|---|---|
| Tool name in `allowed_tools` doesn't match the `@mcp.tool()` function name | `RuntimeError` at worker startup | Exact string match required |
| Forgot to add server to `_MCP_SERVER_CONFIG` | Tool missing from pool, RuntimeError | Add the entry, restart worker |
| Added node to graph but not to `route_from_supervisor` valid set | Supervisor routes there, router sends to fallback | Add name to the `valid` set |
| Added to valid set but not to supervisor prompt | LLM never picks the new route | Add description to `_SUPERVISOR_PROMPT` |
| Built prompt template inside the node function | Rebuilt on every invocation | Move to module-level constant |
| Stored tool reference outside `MCPPool.acquire()` block | Stale session reference, broken tool calls | Resolve tools inside the context manager |
