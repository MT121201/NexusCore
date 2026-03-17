# 🌌 NexusCore: Distributed Multi-Agent System

![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg?logo=fastapi)
![Temporal](https://img.shields.io/badge/Temporal-Orchestration-111111.svg?logo=temporal)
![LangGraph](https://img.shields.io/badge/LangGraph-Agents-orange.svg)
![Docker Ready](https://img.shields.io/badge/Docker-Ready-2496ED.svg?logo=docker)

NexusCore is a production-grade, distributed Multi-Agent System (MAS). Moving beyond simple, fragile LLM wrappers, NexusCore treats AI agents as **durable microservices**. It leverages an asynchronous, event-driven architecture designed to guarantee task completion, independent scaling, and fault tolerance.

## 🚀 Why NexusCore?

Traditional API-driven LLM applications suffer from HTTP timeouts, context loss on pod crashes, and synchronous bottlenecks. NexusCore solves this by decoupling the API ingress from the AI reasoning engine:

* **Fire-and-Forget Ingress:** A stateless FastAPI gateway accepts requests, validates data contracts, and instantly returns a `202 Accepted` with a UUID.
* **Durable Execution:** Temporal orchestrates the workflow. If an AI worker pod crashes mid-thought, Temporal seamlessly resumes the exact state on a new node.
* **Graph-Based Reasoning:** LangGraph powers the "Brain," dynamically routing tasks between specialized agents (Supervisor, DB Agent, Infra Agent) and a Critic for hallucination checks.
* **Lightning Fast Tooling:** Built completely on `uv` for ultra-fast dependency management and environment resolution.

## 🏗️ Architecture

```mermaid
graph TD
    User([User/System Event]) -->|POST /v1/execute| Gateway[FastAPI Gateway]
    Gateway -->|Fire-and-Forget| Temporal{Temporal Orchestrator}
    Gateway -.->|Return TaskID| User

    subgraph "Durable Worker Nodes (Independently Scalable)"
        Temporal -->|Execute| Supervisor[Supervisor Agent - LangGraph]
        Supervisor -->|Delegate| Agents[Parallel Specialist Agents]
        Agents <-->|MCP Protocol| Tools[Postgres/AWS Tools]
        Agents --> Critic[Critic Validation]
        Critic -- "Fail (Hallucination)" --> Supervisor
        Critic -- "Success" --> Finalizer[Synthesis & Report]
    end

    Finalizer -->|Update Status| DB[(Postgres pgvector)]
    Finalizer -->|Broadcast| Redis[Redis Pub/Sub]