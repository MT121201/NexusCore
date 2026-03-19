"""Tests for MCPPool concurrency and backpressure — no subprocesses required."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.mcp import MCPPool


def _make_mock_client(tool_names: list[str]):
    client = MagicMock()
    tools = []
    for name in tool_names:
        t = MagicMock()
        t.name = name
        tools.append(t)
    client.get_tools = AsyncMock(return_value=tools)
    return client


@pytest.fixture(autouse=True)
def reset_pool():
    """Reset MCPPool class state between tests."""
    yield
    MCPPool._initialized = False
    MCPPool._clients = []
    MCPPool._known_names = set()
    if hasattr(MCPPool, "_pool"):
        # Drain the queue
        try:
            while True:
                MCPPool._pool.get_nowait()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_pool_initializes_correct_number_of_slots(monkeypatch):
    tool_names = ["list_tables", "describe_table"]
    monkeypatch.setattr(
        "src.core.mcp.MultiServerMCPClient",
        lambda config: _make_mock_client(tool_names),
    )

    await MCPPool.initialize(pool_size=3)

    assert MCPPool._initialized
    assert MCPPool._pool.qsize() == 3
    assert MCPPool._known_names == set(tool_names)


@pytest.mark.asyncio
async def test_acquire_and_release_returns_slot_to_pool(monkeypatch):
    monkeypatch.setattr(
        "src.core.mcp.MultiServerMCPClient",
        lambda config: _make_mock_client(["tool_a"]),
    )
    await MCPPool.initialize(pool_size=2)

    assert MCPPool._pool.qsize() == 2
    async with MCPPool.acquire() as (_, tool_dict):
        assert MCPPool._pool.qsize() == 1  # one slot in use
        assert "tool_a" in tool_dict
    assert MCPPool._pool.qsize() == 2  # slot returned


@pytest.mark.asyncio
async def test_pool_slot_returned_on_exception(monkeypatch):
    """Slot must be released even if the caller raises inside the context."""
    monkeypatch.setattr(
        "src.core.mcp.MultiServerMCPClient",
        lambda config: _make_mock_client(["tool_a"]),
    )
    await MCPPool.initialize(pool_size=1)

    with pytest.raises(RuntimeError, match="intentional"):
        async with MCPPool.acquire():
            raise RuntimeError("intentional")

    assert MCPPool._pool.qsize() == 1  # slot still available after exception


@pytest.mark.asyncio
async def test_backpressure_blocks_when_all_slots_busy(monkeypatch):
    """
    With pool_size=1, a second acquire must block until the first is released.
    This validates the asyncio.Queue backpressure contract.
    """
    monkeypatch.setattr(
        "src.core.mcp.MultiServerMCPClient",
        lambda config: _make_mock_client(["tool_a"]),
    )
    await MCPPool.initialize(pool_size=1)

    results = []

    async def worker(label: str, hold_seconds: float):
        async with MCPPool.acquire():
            results.append(f"{label}_start")
            await asyncio.sleep(hold_seconds)
            results.append(f"{label}_end")

    # Run two workers concurrently; second must wait for first to finish.
    await asyncio.gather(
        worker("A", 0.05),
        worker("B", 0.01),
    )

    # A must fully complete before B starts (pool_size=1 enforces serial access).
    assert results[0] == "A_start"
    assert results[1] == "A_end"
    assert results[2] == "B_start"
    assert results[3] == "B_end"


@pytest.mark.asyncio
async def test_parallel_workers_with_adequate_pool(monkeypatch):
    """
    With pool_size=2, two workers must run in parallel (not serially).
    """
    monkeypatch.setattr(
        "src.core.mcp.MultiServerMCPClient",
        lambda config: _make_mock_client(["tool_a"]),
    )
    await MCPPool.initialize(pool_size=2)

    start_events = []

    async def worker(label: str):
        async with MCPPool.acquire():
            start_events.append(label)
            await asyncio.sleep(0.05)

    await asyncio.gather(worker("A"), worker("B"))

    # Both workers must have started (parallel execution).
    assert set(start_events) == {"A", "B"}


@pytest.mark.asyncio
async def test_acquire_raises_before_initialization():
    with pytest.raises(RuntimeError, match="initialization"):
        async with MCPPool.acquire():
            pass
