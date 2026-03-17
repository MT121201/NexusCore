# src/core/worker.py
import asyncio
import logging
from temporalio.client import Client
from temporalio.worker import Worker

# Import our workflow and activity
from src.workflows.orchestrator import AgentOrchestratorWorkflow
from src.agents.supervisor import execute_agent_graph

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] Worker: %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    """Starts the Temporal worker to consume background AI tasks."""
    logger.info("Connecting to Temporal server...")

    try:
        # Connect to the local Temporal server (default port 7233)
        client = await Client.connect("localhost:7233")
        logger.info("Successfully connected to Temporal server.")

        # Initialize the worker with our workflow and activity
        worker = Worker(
            client,
            task_queue="nexuscore-task-queue",
            workflows=[AgentOrchestratorWorkflow],
            activities=[execute_agent_graph],
        )

        logger.info("NexusCore Worker is actively listening on 'nexuscore-task-queue'...")

        # Start the worker (this will run indefinitely until killed)
        await worker.run()

    except Exception as e:
        logger.error(f"Worker failed to start or crashed: {str(e)}")


if __name__ == "__main__":
    # Run the async main loop
    asyncio.run(main())