#src/workflows/orchestrator.py
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

# Temporal requires us to explicitly allow imports that might have non-deterministic
# side effects (like datetime or random) when importing inside a workflow file.
with workflow.unsafe.imports_passed_through():
    from src.models.state import TaskRequest, AgentResponse
    import logging

logger = logging.getLogger(__name__)


# ==========================================
# 1. Workflow Definition
# ==========================================

@workflow.defn
class AgentOrchestratorWorkflow:
    """
    The durable workflow that manages the lifecycle of a user request.
    If the worker pod dies while this is running, Temporal will seamlessly
    resume it on another pod.
    """

    @workflow.run
    async def run(self, request: TaskRequest) -> dict:
        workflow.logger.info(f"Starting orchestration for task: {request.idempotency_key}")

        try:
            # Execute the LangGraph Supervisor as a Temporal Activity.
            # We set a generous timeout since LLM chains can take a minute or two.
            # We will build the `execute_agent_graph` activity in the next step.
            final_state = await workflow.execute_activity(
                "execute_agent_graph",
                request,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    non_retryable_error_types=["ValueError", "TypeError"]
                )
            )

            workflow.logger.info(f"Task {request.idempotency_key} completed successfully.")
            return {
                "status": "success",
                "task_id": str(request.idempotency_key),
                "result": final_state
            }

        except Exception as e:
            workflow.logger.error(f"Workflow failed for task {request.idempotency_key}: {str(e)}")
            # In a production scenario, we might trigger a rollback or alert activity here
            return {
                "status": "failed",
                "task_id": str(request.idempotency_key),
                "error": str(e)
            }