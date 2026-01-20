# Copilot Instructions for Enterprise Data Agent

## Architecture Overview

This is a **multi-agent NL2SQL application** using Microsoft Agent Framework (MAF) with a FastAPI backend and Next.js/assistant-ui frontend. Communication happens via SSE streaming, with thread management delegated to Microsoft Foundry.

### Architecture Components
1. **ConversationOrchestrator** (`backend/src/entities/orchestrator/`) - Manages chat sessions, classifies intent (data query vs conversation), handles refinements, invokes NL2SQL workflow
2. **NL2SQLController** (`backend/src/entities/nl2sql_controller/`) - Orchestrates query flow, searches templates via Azure AI Search, executes SQL via `execute_sql` tool
3. **ParameterExtractor** (`backend/src/entities/parameter_extractor/`) - Extracts parameter values from natural language to fill SQL template tokens
4. **ParameterValidator** (`backend/src/entities/parameter_validator/`) - Non-LLM validation of extracted parameters (type, range, regex, allowed values)
5. **QueryValidator** (`backend/src/entities/query_validator/`) - Validates SQL syntax, table allowlist, and security before execution
6. **QueryBuilder** (`backend/src/entities/query_builder/`) - Generates dynamic SQL from table metadata when no template matches

### Architecture Flow
```
User → ConversationOrchestrator (intent classification)
     → NL2SQL Workflow: NL2SQLController → ParameterExtractor/QueryBuilder → Validators → execute_sql
     → ConversationOrchestrator (renders response) → User
```

The ConversationOrchestrator lives **outside** the MAF workflow. It manages the Foundry thread and invokes `create_nl2sql_workflow()` for data queries. This separation allows:
- Session-level conversation context for refinements
- Intent classification before invoking the workflow
- Cleaner separation of concerns

The NL2SQL workflow is built in [backend/src/entities/workflow/workflow.py](backend/src/entities/workflow/workflow.py) and creates a fresh instance per request.

## Key Patterns

### Agent Structure
Each workflow executor follows a consistent pattern in its folder:
- `executor.py` - Workflow integration with `@handler` decorators
- `prompt.md` - Agent instructions (loaded at runtime via `load_prompt()`)
- `tools/` - AI function tools decorated with `@ai_function`

The orchestrator folder contains:
- `orchestrator.py` - ConversationOrchestrator class (not a MAF executor)
- `orchestrator_prompt.md` - Intent classification prompt

### Models Structure
Shared models are in `backend/src/models/` with functional grouping:
- `schema.py` - AI Search index models (`QueryTemplate`, `ParameterDefinition`, `TableMetadata`)
- `extraction.py` - Parameter extraction workflow (`ParameterExtractionRequest`, `MissingParameter`)
- `generation.py` - SQL generation (`SQLDraft`, `SQLDraftMessage`, `QueryBuilderRequest`)
- `execution.py` - Query results (`NL2SQLResponse`)

All models are re-exported from `backend/src/models/__init__.py` for backward compatibility.

### Dual Import Pattern
Agents support both DevUI and FastAPI contexts:
```python
try:
    from models import QueryTemplate  # DevUI (entities on path)
except ImportError:
    from src.models import QueryTemplate  # FastAPI (src on path)
```

### SQLDraft Message Flow
The `SQLDraft` model carries SQL through the validation pipeline. The `SQLDraftMessage` wrapper includes a `source` field to track which executor sent it:
- `source="param_extractor"` - Fresh extraction from template
- `source="param_validator"` - After parameter validation (check `params_validated` flag)
- `source="query_validator"` - After query validation (check `query_validated` flag)
- `source="query_builder"` - Dynamic SQL generation

NL2SQLController routes based on these flags to prevent infinite loops.

### Query Templates
SQL queries are parameterized templates stored in `data/query_templates/` and indexed in Azure AI Search. Parameters use `%{{name}}%` token syntax with validation rules. See `backend/src/models/schema.py` for `QueryTemplate` and `ParameterDefinition` schemas.

### SSE Streaming & Step Events
Step events provide real-time progress to the UI. Emit from tools using:
```python
from src.api.step_events import emit_step_start, emit_step_end
emit_step_start("Executing SQL query...")
# ... do work ...
emit_step_end("Executing SQL query...")
```

## Development Commands

```bash
# Full stack (from frontend folder)
pnpm dev              # Runs both UI and API concurrently

# API only
./scripts/run-api.sh  # FastAPI on :8000 with hot reload
./scripts/setup-api.sh # Create venv and install deps

# Frontend only  
pnpm dev:ui           # Next.js with Turbopack

# DevUI for agent testing (from backend folder)
devui ./src/entities  # Test agents in isolation
```

## Environment Configuration

API requires `.env` in `backend/` folder with:
- `AZURE_AI_PROJECT_ENDPOINT` - Foundry project endpoint (required)
- `AZURE_AI_MODEL_DEPLOYMENT_NAME` - Default model deployment
- `AZURE_SEARCH_ENDPOINT` - AI Search for query templates
- `AZURE_SQL_*` - Database connection settings
- `APPLICATIONINSIGHTS_CONNECTION_STRING` - For tracing (optional)
- `ENABLE_INSTRUMENTATION=true` - Enable Application Insights tracing

## Frontend Integration

- Uses `assistant-ui` library with `ExternalStoreRuntime` for SSE
- Thread IDs come from Foundry (no local session management)
- Tool results render via generative UI components in `frontend/components/assistant-ui/`
- Auth via MSAL with optional Azure AD (`frontend/lib/msalConfig.ts`)

## API Structure

```
backend/src/
├── api/                    # FastAPI application
│   ├── main.py            # App entrypoint
│   ├── middleware/        # Auth middleware
│   ├── routers/           # API routes
│   └── step_events.py     # SSE step event helpers
├── entities/              # Agent executors (DevUI compatible)
│   ├── orchestrator/
│   ├── nl2sql_controller/
│   ├── parameter_extractor/
│   ├── parameter_validator/
│   ├── query_builder/
│   ├── query_validator/
│   ├── shared/            # Shared utilities (search_client)
│   └── workflow/
└── models/                # Pydantic models
    ├── schema.py          # AI Search index models
    ├── extraction.py      # Parameter extraction
    ├── generation.py      # SQL generation
    └── execution.py       # Query results
```

## Testing Strategy

Tests use `pytest` with `pytest-asyncio`. Key test scenarios to cover:

1. **Empty Results** - Queries that succeed but return no rows (e.g., "show orders from 2050")
2. **Invalid Queries** - SQL execution failures, malformed input, permission errors
3. **Clarification Prompts** - When ParameterExtractor can't infer values and `ask_if_missing=true`
4. **Parameter Validation** - Invalid parameter types, out-of-range values, regex failures
5. **Query Validation** - Disallowed tables, SQL injection patterns, non-SELECT statements

Run tests: `cd backend && pytest`

## Adding New Capabilities

When adding a new tool to an agent:
1. Create function in agent's `tools/` folder with `@ai_function` decorator
2. Import and add to agent's `tools=[]` list in `executor.py`
3. Update the agent's `prompt.md` to describe when/how to use it
4. For progress feedback, emit step events from within the tool

When modifying workflow routing:
- ChatAgent's `prompt.md` controls the triage logic (JSON routing vs direct response)
- DataAgent's `handle_sql_draft` routes based on `SQLDraft` flags (`params_validated`, `query_validated`)
- Add new executors in `workflow.py` with bidirectional edges to DataAgent
