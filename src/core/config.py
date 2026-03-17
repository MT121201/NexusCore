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

    # LLM Integrations
    openai_api_key: str = ""

    # This tells Pydantic to look for a .env file in the root directory
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/nexuscore"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


# Instantiate a global settings object to be imported across the app
settings = Settings()