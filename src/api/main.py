# src/api/main.py
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError

from src.core.events import redis_client
from src.models.state import TaskRequest, TaskResponse
from src.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

temporal_client: Client | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global temporal_client
    logger.info("Initializing API Gateway…")
    try:
        temporal_client = await Client.connect(
            settings.temporal_host,
            data_converter=pydantic_data_converter,
        )
        logger.info(f"Connected to Temporal at {settings.temporal_host}.")
    except Exception as e:
        logger.error(f"Failed to connect to Temporal: {e}")
    yield
    logger.info("Shutting down API Gateway…")


app = FastAPI(
    title="NexusCore API Gateway",
    description="Ingress point for the NexusCore Distributed Multi-Agent System.",
    version="1.0.0-PROD",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _dispatch_workflow(request: TaskRequest, client: Client) -> None:
    """
    Submit a Temporal workflow. WorkflowAlreadyStartedError is treated as a
    successful idempotent re-submission — same task_id, same result.
    """
    try:
        await client.start_workflow(
            "AgentOrchestratorWorkflow",
            request,
            id=str(request.idempotency_key),
            task_queue=settings.temporal_task_queue,
        )
        logger.info(f"Workflow {request.idempotency_key} dispatched.")
    except RPCError as e:
        # gRPC ALREADY_EXISTS → idempotent re-submission, not an error.
        if "already exists" in str(e).lower():
            logger.info(f"Workflow {request.idempotency_key} already running — idempotent skip.")
        else:
            logger.error(f"Failed to dispatch workflow {request.idempotency_key}: {e}")
    except Exception as e:
        logger.error(f"Failed to dispatch workflow {request.idempotency_key}: {e}")


@app.post("/v1/execute", response_model=TaskResponse, status_code=202)
async def execute_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """Ingest a task and asynchronously trigger the orchestration workflow."""
    if not temporal_client:
        raise HTTPException(status_code=503, detail="Temporal client not connected.")

    logger.info(f"Received request from user: {request.user_id}")
    background_tasks.add_task(_dispatch_workflow, request, temporal_client)

    return TaskResponse(
        task_id=request.idempotency_key,
        status="accepted",
        message="Task queued for orchestration.",
    )


@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "healthy", "service": "nexuscore-api-gateway"}


@app.websocket("/ws/task/{task_id}")
async def task_status_websocket(websocket: WebSocket, task_id: str):
    """
    Streams real-time agent events to the client via WebSocket.
    Uses Redis pubsub.listen() (event-driven) instead of polling to avoid
    O(N×10) Redis no-ops per second across concurrent connections.
    """
    await websocket.accept()

    pubsub = redis_client.pubsub()
    channel_name = f"task_updates:{task_id}"
    await pubsub.subscribe(channel_name)

    try:
        await websocket.send_json({"type": "system", "message": f"Connected to stream for {task_id}"})

        async for message in pubsub.listen():
            if message["type"] != "message":
                # Skip subscribe/unsubscribe control messages.
                continue
            data = message["data"].decode("utf-8")
            await websocket.send_text(data)

    except WebSocketDisconnect:
        logger.info(f"Client disconnected from task stream {task_id}.")
    except Exception as e:
        logger.error(f"WebSocket error for task {task_id}: {e}")
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.aclose()
