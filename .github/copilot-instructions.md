# Copilot Instructions for Cadence

Trust these instructions first; only search if information is incomplete or incorrect.

## Quick Reference

| What                | Where               |
| ------------------- | ------------------- |
| **Package manager** | `uv` (NOT pip)      |
| **Task runner**     | `uv run poe <task>` |
| **All checks**      | `uv run poe check`  |
| **Python version**  | 3.11+               |
| **Line length**     | 100 chars            |

## Essential Commands

```bash
uv run poe check     # Run ALL quality checks (required before commit)
uv run poe test      # Run tests
uv run poe lint      # Lint only
uv run poe format    # Format, lint, and type check
uv run poe dev-api   # Start FastAPI dev server
```

## Documentation (Read These)

| Document                                    | Purpose                             |
| ------------------------------------------- | ----------------------------------- |
| [CODING_STANDARD.md](../CODING_STANDARD.md) | Anti-slop rules, forbidden patterns |
| [DEV_SETUP.md](../DEV_SETUP.md)             | Environment setup, all poe tasks    |
| [AGENTS.md](../AGENTS.md)                   | AI agent quick reference            |
| [CONTRIBUTING.md](../CONTRIBUTING.md)       | Git conventions, PR guidelines      |

## Path-Specific Instructions

You **MUST** load these instructions when working on files that match the patterns below.

| File Pattern    | Instructions                                                  |
| --------------- | ------------------------------------------------------------- |
| `**/*.py`       | [python.instructions.md](instructions/python.instructions.md) |
| `**/*.agent.md` | [agents.instructions.md](instructions/agents.instructions.md) |
| Git files       | [git.instructions.md](instructions/git.instructions.md)       |
| Task tracking   | [tasks.instructions.md](instructions/tasks.instructions.md)   |

## Custom Agents

9 specialized agents in `.github/agents/`. See [agents.instructions.md](instructions/agents.instructions.md) for usage.

Use `@agent-name` in Copilot Chat: `@orchestrator`, `@planner`, `@architect`, `@implementer`, `@tester`, `@reviewer`, `@security`, `@infrastructure`, `@docs`

## Non-Negotiable Rules

1. **Async-first**: Use `async def` for I/O, never block the event loop
2. **Type hints** on all parameters and returns
3. **Pydantic models** for all I/O (no raw dicts)
4. **`uv run poe check`** must pass before commit
5. **Conventional Commits**: `type(scope): description`

## Task/Issue Tracking

This project uses **bd (beads)** for issue tracking.

```bash
bd ready              # Find unblocked work
bd ready --json       # Get ready tasks as JSON
bd create "Title" --type task --priority 2  # Create issue
bd update <id> --status in_progress  # Claim task
bd close <id> --reason "Done"   # Complete task
bd sync               # Sync with git (run at session end)
```

---

## Architecture Overview

This is a **multi-agent NL2SQL application** using Microsoft Agent Framework (MAF) with a FastAPI backend and Next.js/assistant-ui frontend. Communication happens via SSE streaming, with thread management delegated to Microsoft Foundry.

### Architecture Components
1. **ConversationOrchestrator** (`src/backend/entities/orchestrator/`) - Manages chat sessions, classifies intent (data query vs conversation), handles refinements, invokes NL2SQL workflow
2. **NL2SQLController** (`src/backend/entities/nl2sql_controller/`) - Orchestrates query flow, searches templates via Azure AI Search, executes SQL via `execute_sql` tool
3. **ParameterExtractor** (`src/backend/entities/parameter_extractor/`) - Extracts parameter values from natural language to fill SQL template tokens
4. **ParameterValidator** (`src/backend/entities/parameter_validator/`) - Non-LLM validation of extracted parameters (type, range, regex, allowed values)
5. **QueryValidator** (`src/backend/entities/query_validator/`) - Validates SQL syntax, table allowlist, and security before execution
6. **QueryBuilder** (`src/backend/entities/query_builder/`) - Generates dynamic SQL from table metadata when no template matches

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

The NL2SQL workflow is built in `src/backend/entities/workflow/workflow.py` and creates a fresh instance per request.

## Key Patterns

### Agent Structure
Each workflow executor follows a consistent pattern in its folder:
- `executor.py` - Workflow integration with `@handler` decorators
- `prompt.md` - Agent instructions (loaded at runtime via `load_prompt()`)
- `tools/` - AI function tools decorated with `@tool`

The orchestrator folder contains:
- `orchestrator.py` - ConversationOrchestrator class (not a MAF executor)
- `orchestrator_prompt.md` - Intent classification prompt

### Models Structure
Shared models are in `src/backend/models/` with functional grouping:
- `schema.py` - AI Search index models (`QueryTemplate`, `ParameterDefinition`, `TableMetadata`)
- `extraction.py` - Parameter extraction workflow (`ParameterExtractionRequest`, `MissingParameter`)
- `generation.py` - SQL generation (`SQLDraft`, `SQLDraftMessage`, `QueryBuilderRequest`)
- `execution.py` - Query results (`NL2SQLResponse`)

All models are re-exported from `src/backend/models/__init__.py` for backward compatibility.

### Import Pattern
With `src/backend/` on the Python path, all imports use the package directly:
```python
from models import QueryTemplate
from entities.shared.search_client import AzureSearchClient
from api.step_events import emit_step_start
```

### SQLDraft Message Flow
The `SQLDraft` model carries SQL through the validation pipeline. The `SQLDraftMessage` wrapper includes a `source` field to track which executor sent it:
- `source="param_extractor"` - Fresh extraction from template
- `source="param_validator"` - After parameter validation (check `params_validated` flag)
- `source="query_validator"` - After query validation (check `query_validated` flag)
- `source="query_builder"` - Dynamic SQL generation

NL2SQLController routes based on these flags to prevent infinite loops.

### Query Templates
SQL queries are parameterized templates stored in `infra/data/query_templates/` and indexed in Azure AI Search. Parameters use `%{{name}}%` token syntax with validation rules. See `src/backend/models/schema.py` for `QueryTemplate` and `ParameterDefinition` schemas.

### SSE Streaming & Step Events
Step events provide real-time progress to the UI. Emit from tools using:
```python
from api.step_events import emit_step_start, emit_step_end
emit_step_start("Executing SQL query...")
# ... do work ...
emit_step_end("Executing SQL query...")
```

## Environment Configuration

API requires `.env` in `src/backend/` folder with:
- `AZURE_AI_PROJECT_ENDPOINT` - Foundry project endpoint (required)
- `AZURE_AI_MODEL_DEPLOYMENT_NAME` - Default model deployment
- `AZURE_SEARCH_ENDPOINT` - AI Search for query templates
- `AZURE_SQL_*` - Database connection settings
- `APPLICATIONINSIGHTS_CONNECTION_STRING` - For tracing (optional)
- `ENABLE_INSTRUMENTATION=true` - Enable Application Insights tracing

## Frontend Integration

- Uses `assistant-ui` library with `ExternalStoreRuntime` for SSE
- Thread IDs come from Foundry (no local session management)
- Tool results render via generative UI components in `src/frontend/components/assistant-ui/`
- Auth via MSAL with optional Azure AD (`src/frontend/lib/msalConfig.ts`)

## Project Structure

```
cadence/
├── src/                        # All application code
│   ├── backend/               # Python backend
│   │   ├── __init__.py
│   │   ├── Dockerfile
│   │   ├── api/               # FastAPI application
│   │   │   ├── main.py        # App entrypoint
│   │   │   ├── middleware/    # Auth middleware
│   │   │   ├── routers/       # API routes
│   │   │   └── step_events.py # SSE step event helpers
│   │   ├── entities/          # Agent executors
│   │   │   ├── orchestrator/
│   │   │   ├── nl2sql_controller/
│   │   │   ├── parameter_extractor/
│   │   │   ├── parameter_validator/
│   │   │   ├── query_builder/
│   │   │   ├── query_validator/
│   │   │   ├── shared/        # Shared utilities (search_client)
│   │   │   └── workflow/
│   │   └── models/            # Pydantic models
│   └── frontend/              # Next.js + assistant-ui
├── infra/                      # Terraform IaC
│   ├── data/                  # Query templates, table metadata
│   └── scripts/               # Shell scripts
├── tests/                     # Test suite
│   ├── unit/
│   └── integration/
├── pyproject.toml             # Python config (deps, tools, linting)
├── devsetup.sh                # One-command dev setup
└── .github/                   # CI/CD, agents, instructions
```

## Testing Strategy

Tests use `pytest` with `pytest-asyncio`. Key test scenarios to cover:

1. **Empty Results** - Queries that succeed but return no rows (e.g., "show orders from 2050")
2. **Invalid Queries** - SQL execution failures, malformed input, permission errors
3. **Clarification Prompts** - When ParameterExtractor can't infer values and `ask_if_missing=true`
4. **Parameter Validation** - Invalid parameter types, out-of-range values, regex failures
5. **Query Validation** - Disallowed tables, SQL injection patterns, non-SELECT statements

Run tests: `uv run poe test`

## Adding New Capabilities

When adding a new tool to an agent:
1. Create function in agent's `tools/` folder with `@tool` decorator
2. Import and add to agent's `tools=[]` list in `executor.py`
3. Update the agent's `prompt.md` to describe when/how to use it
4. For progress feedback, emit step events from within the tool

When modifying workflow routing:
- ChatAgent's `prompt.md` controls the triage logic (JSON routing vs direct response)
- DataAgent's `handle_sql_draft` routes based on `SQLDraft` flags (`params_validated`, `query_validated`)
- Add new executors in `workflow.py` with bidirectional edges to DataAgent
