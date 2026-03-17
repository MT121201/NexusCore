# src/core/worker.py
import asyncio
import logging
from temporalio.client import Client
from temporalio.worker import Worker

# Import our workflow and activity
from src.workflows.orchestrator import AgentOrchestratorWorkflow
from src.agents.supervisor import execute_agent_graph
from src.core.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] Worker: %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    """Starts the Temporal worker to consume background AI tasks."""
    logger.info(f"Connecting to Temporal server at {settings.temporal_host}...")

    try:
        # Use settings instead of hardcoded values
        client = await Client.connect(settings.temporal_host)
        logger.info("Successfully connected to Temporal server.")

        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,  # <-- Here
            workflows=[AgentOrchestratorWorkflow],
            activities=[execute_agent_graph],
        )

        logger.info(f"NexusCore Worker is actively listening on '{settings.temporal_task_queue}'...")

        await worker.run()

    except Exception as e:
        logger.error(f"Worker failed to start or crashed: {str(e)}")


if __name__ == "__main__":
    # Run the async main loop
    asyncio.run(main())