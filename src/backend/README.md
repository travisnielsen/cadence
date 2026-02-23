# Cadence Backend

FastAPI backend for the Cadence NL2SQL application, powered by [Microsoft Agent Framework (MAF)](https://github.com/microsoft/agent-framework).

## Architecture Overview

### API Sequence Diagram

The following diagram shows the key API interactions between the frontend and backend:

```mermaid
%%{init: {'theme': 'dark'}}%%
sequenceDiagram
    participant Client as Frontend<br/>assistant-ui
    participant API as FastAPI
    participant MAF as Orchestration<br/>Agent Framework
    participant Provider as Agent Service / Responses API

    rect rgb(50, 40, 60)
        Note over Client, Foundry: Chat Interaction (SSE Stream)
        Client->>API: Send message<br/>(with access_token)
        API->>API: Validate token
        API->>MAF: Execute workflow
        activate MAF
        MAF->>Provider: Create/restore session context (if needed)
        Provider-->>MAF: Provider conversation/session handle
        MAF->>Provider: Agent + Responses operations

        loop Real-time Updates
            MAF-->>Client: Step progress events
            MAF-->>Client: Tool execution results
            MAF-->>Client: Streamed content
        end

        MAF-->>Client: conversation_id + completion
        deactivate MAF
    end

    rect rgb(30, 50, 70)
        Note over Client, Provider: Conversation Management
        Client->>API: List user conversations
        API->>Provider: Query conversations
        Provider-->>Client: Conversation list with metadata

        Client->>API: Load conversation history
        API->>Provider: Fetch messages
        Provider-->>Client: Conversation messages

        Client->>API: Update conversation (title/status)
        Client->>API: Delete conversation
        API-->>Client: Confirmation
    end
```

### Conversation Continuity

Conversation continuity in the chat stream follows this order:

1. If the client provides `conversation_id`, backend reuses it as the provider conversation ID.
2. If missing, backend pre-creates a provider conversation via the OpenAI client and uses that `id`.
3. `DataAssistant` resumes the thread with `agent.get_session(service_session_id=conversation_id)`.
4. SSE responses emit this same `conversation_id` to the client for subsequent turns.
5. If provider ID creation fails, continuity temporarily falls back to local `AgentSession.session_id`.

### Agent Workflow

The application uses a multi-agent workflow to process user queries:

```mermaid
%%{init: {'theme': 'dark'}}%%
flowchart TB
    subgraph Workflow["NL2SQL Workflow"]
        direction TB

        subgraph ChatAgent["Chat Agent"]
            CA_DESC["• Receives user messages<br/>• Triages requests<br/>• Renders final responses"]
        end

        subgraph DataAgent["Data Agent (NL2SQL)"]
            DA_DESC["• Match user intent to SQL template<br/>• Executes SQL queries<br/>• Returns structured results"]
            DA_TOOLS["Tools: search_templates, execute_sql"]
        end

        subgraph ParamExtractor["Parameter Extractor"]
            PE_DESC["• Extracts parameter values from user input<br/>• Fills SQL template tokens"]
        end
    end

    User([User Query]) --> ChatAgent
    ChatAgent -->|"Data question"| DataAgent
    DataAgent -->|"Template match found"| ParamExtractor
    ParamExtractor -->|"Extracted parameters"| DataAgent
    DataAgent -->|"Query results"| ChatAgent
    ChatAgent --> Response([Rendered Response])

    style Workflow fill:#1a1a2e,stroke:#4a4a6a
    style ChatAgent fill:#32284d,stroke:#6b5b95
    style DataAgent fill:#1e3246,stroke:#4a7c9b
    style ParamExtractor fill:#28473d,stroke:#5a9a7a

```

### Workflow Agents

| Agent | Purpose |
|-------|---------|
| **Chat Agent** | User-facing agent that receives messages, triages them (data vs. general questions), and renders the final response with helpful context |
| **Data Agent (NL2SQL)** | Searches for query templates matching user intent, executes SQL against the database, and returns structured results |
| **Parameter Extractor** | When a query template is matched, extracts parameter values from the user's natural language input to fill SQL template tokens |

### Agent Components

| Component | Path | Purpose |
|-----------|------|---------|
| **API Layer** | `api/` | FastAPI routes, middleware, SSE streaming |
| **DataAssistant** | `entities/assistant/` | Session management, intent classification, response rendering |
| **NL2SQL Pipeline** | `entities/nl2sql_controller/` | Query pipeline (`process_query`), template search, SQL execution |
| **ParameterExtractor** | `entities/parameter_extractor/` | Extracts parameter values from natural language to fill SQL template tokens |
| **ParameterValidator** | `entities/parameter_validator/` | Non-LLM validation of parameters (type, range, regex, allowed values) |
| **QueryBuilder** | `entities/query_builder/` | Dynamic SQL generation from table metadata when no template matches |
| **QueryValidator** | `entities/query_validator/` | SQL syntax validation, table allowlist, security checks |
| **Pipeline Clients** | `entities/workflow/` | Protocol-based DI container (`PipelineClients`), adapter factories |

### API Endpoints

| Route | Description |
|-------|-------------|
| `POST /chat` | SSE streaming chat endpoint |
| `GET /conversations` | List conversations |
| `GET /conversations/{id}` | Load conversation metadata |
| `GET /conversations/{id}/messages` | Load conversation messages |
| `DELETE /conversations/{id}` | Delete a conversation |
| `GET /health` | Health check |

## Development

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- SQL Server with WideWorldImporters database
- Azure AI Search instance (for query templates)
- Azure AI Foundry project endpoint

### Setup

From the repository root:

```bash
./devsetup.sh          # One-command setup (installs uv, creates venv, installs deps)
cp src/backend/.env.example src/backend/.env  # Configure environment variables
```

### Running

```bash
uv run poe dev-api     # Start FastAPI dev server with hot reload
```

### Environment Variables

Copy `.env.example` to `.env` and configure:

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_AI_PROJECT_ENDPOINT` | Yes | Foundry project endpoint |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Yes | Default model deployment |
| `AZURE_SEARCH_ENDPOINT` | Yes | AI Search endpoint for query templates |
| `AZURE_SQL_SERVER` | Yes | SQL Server hostname |
| `AZURE_SQL_DATABASE` | Yes | Database name |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | No | Application Insights for tracing |
| `ENABLE_INSTRUMENTATION` | No | Set `true` to enable OpenTelemetry |

### Quality Checks

```bash
uv run poe check       # Run all checks (lint + typecheck)
uv run poe test        # Run tests with coverage
uv run poe lint        # Ruff linting only
uv run poe format      # Format, lint, and typecheck
uv run poe typecheck   # basedpyright type checking
```

## Docker

The Dockerfile uses a multi-stage build:

1. **Builder stage**: Installs dependencies via `uv sync`
2. **Runtime stage**: Slim image with ODBC driver for SQL Server, application code only

```bash
docker build -f src/backend/Dockerfile -t cadence-backend .
docker run -p 8000:8000 --env-file src/backend/.env cadence-backend
```

Build context is the repository root.
