import asyncio
import logging
from temporalio.client import Client
from temporalio.worker import Worker

from src.workflows.orchestrator import AgentOrchestratorWorkflow
from src.workflows.graph import execute_agent_graph
from src.core.config import settings
from src.core.mcp import MCPPool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] Worker: %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    """Starts the Temporal worker to consume background AI tasks."""
    logger.info(f"Connecting to Temporal at {settings.temporal_host}…")

    try:
        # 1. Spin up the MCP subprocess pool.
        #    pool_size controls max concurrent tool executions (no more serialized stdio).
        await MCPPool.initialize(pool_size=settings.mcp_pool_size)

        # 2. Connect to Temporal.
        client = await Client.connect(settings.temporal_host)
        logger.info("Connected to Temporal.")

        # 3. Start worker with explicit concurrency caps so bursts don't overload
        #    the MCP pool or exhaust OpenAI rate limits.
        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[AgentOrchestratorWorkflow],
            activities=[execute_agent_graph],
            max_concurrent_activities=settings.temporal_max_concurrent_activities,
            max_concurrent_workflow_tasks=settings.temporal_max_concurrent_workflow_tasks,
        )

        logger.info(
            f"NexusCore Worker listening on '{settings.temporal_task_queue}' "
            f"(max_activities={settings.temporal_max_concurrent_activities}, "
            f"mcp_pool={settings.mcp_pool_size})…"
        )
        await worker.run()

    except Exception as e:
        logger.error(f"Worker crashed: {e}", exc_info=True)
    finally:
        await MCPPool.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
