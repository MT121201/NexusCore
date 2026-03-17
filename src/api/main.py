# src/api/main.py
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from temporalio.client import Client

from src.models.state import TaskRequest, TaskResponse

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Global reference for our Temporal client
temporal_client: Client | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager to establish persistent connections on startup."""
    global temporal_client
    logger.info("Initializing API Gateway...")
    try:
        # Connect to the Temporal cluster
        temporal_client = await Client.connect("localhost:7233")
        logger.info("Successfully connected to Temporal cluster.")
    except Exception as e:
        logger.error(f"Failed to connect to Temporal: {e}")
        # In production, we might want to crash the pod here if Temporal is strictly required

    yield  # App runs here

    logger.info("Shutting down API Gateway...")


# Initialize FastAPI app with the lifespan manager
app = FastAPI(
    title="NexusCore API Gateway",
    description="Ingress point for the NexusCore Distributed Multi-Agent System.",
    version="1.0.0-PROD",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def trigger_temporal_workflow(request: TaskRequest, client: Client):
    """Background task to signal the Temporal Orchestrator."""
    try:
        logger.info(f"Dispatching workflow for Task: {request.idempotency_key}")

        # Actually start the workflow on the Temporal cluster
        await client.start_workflow(
            "AgentOrchestratorWorkflow",
            request,
            id=str(request.idempotency_key),
            task_queue="nexuscore-task-queue",
        )
        logger.info(f"Workflow {request.idempotency_key} successfully dispatched.")
    except Exception as e:
        logger.error(f"Failed to dispatch workflow: {e}")


@app.post("/v1/execute", response_model=TaskResponse, status_code=202)
async def execute_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """Ingests a task request and asynchronously triggers the workflow."""
    global temporal_client

    if not temporal_client:
        raise HTTPException(status_code=503, detail="Temporal client not connected. Please try again later.")

    try:
        logger.info(f"Received execution request from user: {request.user_id}")
        background_tasks.add_task(trigger_temporal_workflow, request, temporal_client)

        return TaskResponse(
            task_id=request.idempotency_key,
            status="accepted",
            message="Task queued for orchestration."
        )
    except Exception as e:
        logger.error(f"Failed to ingest task: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error during task ingestion.")


@app.get("/health", tags=["System"])
async def health_check():
    """Liveness probe."""
    return {"status": "healthy", "service": "nexuscore-api-gateway"}