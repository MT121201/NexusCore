# =============================================================
# Stage 1: Builder — install all Python dependencies via uv
# =============================================================
FROM python:3.12-slim AS builder

# Copy uv binary from the official image (no install script needed)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install dependencies from the lockfile.
# This layer is cached as long as pyproject.toml and uv.lock are unchanged.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

# Copy application source into the builder stage
COPY src/ ./src/

# =============================================================
# Stage 2: Runtime — lean image without build tooling
# =============================================================
FROM python:3.12-slim AS runtime

WORKDIR /app

# Non-root user for security
RUN groupadd -r nexuscore && useradd -r -g nexuscore -u 1001 nexuscore

# Copy the virtual environment and source from builder
COPY --from=builder --chown=nexuscore:nexuscore /app/.venv /app/.venv
COPY --from=builder --chown=nexuscore:nexuscore /app/src  /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    # Required so `python -m src.*` imports resolve correctly in subprocesses
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER nexuscore

EXPOSE 8000

# Default entrypoint is the API gateway.
# The worker container overrides this with `command:` in docker-compose.
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
