import sys
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

# Server config defined once; each pool slot spawns its own independent subprocesses.
_MCP_SERVER_CONFIG = {
    "postgres_mcp": {
        "command": sys.executable,
        "args": ["-m", "src.mcp.postgres_server"],
        "transport": "stdio",
    },
    "aws_mcp": {
        "command": sys.executable,
        "args": ["-m", "src.mcp.aws_server"],
        "transport": "stdio",
    },
}

# Each pool slot: (client, tool_dict) where tool_dict binds tool name → BaseTool
# bound to that specific client session.
PoolEntry = tuple[MultiServerMCPClient, dict[str, BaseTool]]


class MCPPool:
    """
    Pool of MCP subprocess sets. Each slot owns its own independent pair of
    subprocesses (postgres_mcp + aws_mcp), so N concurrent agent tasks can
    execute tools in parallel without serializing on a single stdio pipe.

    Usage:
        async with MCPPool.acquire() as (_, tool_dict):
            result = await tool_dict["list_tables"].ainvoke({})
    """

    _pool: asyncio.Queue  # Queue[PoolEntry]
    _clients: list[MultiServerMCPClient] = []
    _known_names: set[str] = set()
    _initialized: bool = False
    _lock = asyncio.Lock()

    @classmethod
    async def initialize(cls, pool_size: int) -> None:
        async with cls._lock:
            if cls._initialized:
                return

            logger.info(f"Initializing MCP pool with {pool_size} slot(s)…")
            cls._pool = asyncio.Queue(maxsize=pool_size)
            cls._clients = []

            for i in range(pool_size):
                client = MultiServerMCPClient(_MCP_SERVER_CONFIG)
                tools = await client.get_tools()
                tool_dict: dict[str, BaseTool] = {t.name: t for t in tools}
                await cls._pool.put((client, tool_dict))
                cls._clients.append(client)
                logger.info(f"MCP pool slot {i + 1}/{pool_size} ready — {len(tool_dict)} tools.")

            # Capture names from the first slot for validation use (O(1) lookup later).
            first_entry: PoolEntry = cls._pool.get_nowait()
            cls._known_names = set(first_entry[1].keys())
            cls._pool.put_nowait(first_entry)

            cls._initialized = True
            logger.info(f"MCP pool ready. Tools available: {sorted(cls._known_names)}")

    @classmethod
    @asynccontextmanager
    async def acquire(cls) -> AsyncGenerator[PoolEntry, None]:
        """
        Block until a pool slot is free, yield (client, tool_dict), then return
        the slot to the pool. Natural backpressure: callers wait rather than
        spawning unbounded subprocesses.
        """
        if not cls._initialized:
            raise RuntimeError("MCPPool accessed before initialization. Call MCPPool.initialize() first.")

        entry: PoolEntry = await cls._pool.get()
        try:
            yield entry
        finally:
            cls._pool.put_nowait(entry)

    @classmethod
    async def disconnect(cls) -> None:
        for client in cls._clients:
            if hasattr(client, "aclose"):
                await client.aclose()
        cls._clients = []
        cls._initialized = False
        logger.info("MCP pool shut down cleanly.")
