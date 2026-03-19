#src/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralized configuration manager for NexusCore.
    Reads from environment variables or a .env file.
    """
    environment: str = "development"
    api_port: int = 8000

    # Temporal
    temporal_host: str = "localhost:7233"
    temporal_task_queue: str = "nexuscore-task-queue"
    temporal_max_concurrent_activities: int = 10
    temporal_max_concurrent_workflow_tasks: int = 20

    # LLM
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_max_retries: int = 3

    # MCP subprocess pool — each slot is an independent set of subprocesses
    mcp_pool_size: int = 4

    # Supervisor routing gate
    supervisor_confidence_threshold: float = 0.7

    database_url: str = "postgresql://postgres:postgres@localhost:5432/nexuscore"

    redis_url: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


# Instantiate a global settings object to be imported across the app
settings = Settings()