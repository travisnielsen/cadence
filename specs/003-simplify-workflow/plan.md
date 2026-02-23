# Implementation Plan: Simplify NL2SQL Workflow

**Branch**: `003-simplify-workflow` | **Date**: 2026-02-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-simplify-workflow/spec.md`

## Summary

Replace the MAF Executor/Workflow/WorkflowBuilder orchestration layer with plain async Python functions while keeping `ChatAgent`, `AzureAIClient`, session handling via `AgentSession`, and `@tool` for LLM calls and provider integration. The NL2SQL pipeline becomes a single `process_query()` async function that calls extracted sub-functions directly instead of routing messages through a WorkflowContext graph.

Additionally, introduce **Protocol-based dependency injection** for all I/O boundaries (search, SQL execution, progress reporting) so extracted functions are unit-testable without `unittest.mock.patch` hacks. Centralize configuration into a `Settings` model and rename `ConversationOrchestrator` → `DataAssistant` to better reflect its actual responsibility as a data-focused assistant (not an orchestrator).

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI, Microsoft Agent Framework (ChatAgent + AzureAIClient only), Pydantic
**Storage**: Azure SQL (via execute_sql tool), Azure AI Search (via search tools)
**Testing**: pytest + pytest-asyncio
**Target Platform**: Linux container (Azure Container Apps)
**Project Type**: Web (backend + frontend)
**Performance Goals**: No regression — same SSE latency, same Foundry agent registration behavior
**Constraints**: Zero API contract changes — frontend must not need modifications
**Scale/Scope**: ~4,744 lines across 8 files → target ~1,900 lines across ~6 files
**Testability Goal**: All extracted functions testable with zero `sys.modules` hacks, zero `mock.patch` of module globals, and zero Azure credentials

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I. Async-First | ✅ Pass | Extracted functions remain `async def` for I/O-bound operations. Pure validators become sync. |
| II. Validated Data at Boundaries | ✅ Pass | All Pydantic models (`NL2SQLResponse`, `SQLDraft`, `ClarificationRequest`) are unchanged. |
| III. Fully Typed | ✅ Pass | Function signatures gain explicit parameter/return types — improvement over `WorkflowContext` `Any` typing. |
| IV. Single-Responsibility Executors | ✅ Pass | Each function keeps single responsibility — but as a function, not a class. Simpler, same principle. |
| V. Automated Quality Gates | ✅ Pass | `uv run poe check` must pass at every phase checkpoint. |

## Testability Architecture

The current codebase has significant testability gaps caused by tight coupling to Azure services, module-level side effects, and `ContextVar`-based implicit state. This section defines the patterns that all extracted functions must follow.

### Problem: Import-Time Side Effects

Every executor imports `agent_framework` at the top level, and module-level code like `ALLOWED_TABLES = _load_allowed_tables()` runs filesystem I/O at import time. Tests currently work around this with `sys.modules` stubbing and `importlib.util.spec_from_file_location` hacks — fragile boilerplate copied into every test file.

**Rule**: No extracted module may perform I/O (network, filesystem, env vars) at import time. All I/O dependencies are injected via function parameters or constructor arguments.

### Protocol Interfaces for I/O Boundaries

Define `Protocol` classes for the three external I/O boundaries so tests can provide in-memory fakes:

```python
# src/backend/entities/shared/protocols.py (NEW)

from typing import Protocol, runtime_checkable

@runtime_checkable
class TemplateSearchService(Protocol):
    async def search(self, query: str) -> list[QueryTemplate]: ...

@runtime_checkable
class TableSearchService(Protocol):
    async def search(self, query: str) -> list[TableMetadata]: ...

@runtime_checkable
class SqlExecutor(Protocol):
    async def execute(self, sql: str, params: dict[str, str]) -> list[dict]: ...
    async def execute_parameterized(self, query: ParameterizedQuery) -> list[dict]: ...
```

Production implementations wrap `AzureSearchClient` and `AzureSqlClient`. Tests provide simple fakes that return canned data — no `mock.patch` needed.

### ProgressReporter Protocol

Replace the scattered `try: from api.step_events import ...; except ImportError: pass` pattern with an injectable protocol:

```python
# src/backend/entities/shared/protocols.py

@runtime_checkable
class ProgressReporter(Protocol):
    def step_start(self, name: str) -> None: ...
    def step_end(self, name: str) -> None: ...

class NoOpReporter:
    """Silent reporter for tests and contexts without SSE streaming."""
    def step_start(self, name: str) -> None: ...
    def step_end(self, name: str) -> None: ...

class QueueReporter:
    """Production reporter that writes to the request's asyncio.Queue."""
    def __init__(self, queue: asyncio.Queue) -> None: ...
    def step_start(self, name: str) -> None:
        # Same logic as current emit_step_start()
        ...
    def step_end(self, name: str) -> None:
        # Same logic as current emit_step_end()
        ...
```

Every extracted function accepts `reporter: ProgressReporter = NoOpReporter()` — tests get silent reporting by default, production passes `QueueReporter`.

### Centralized Settings

Replace scattered `os.getenv()` calls with a single Pydantic settings model:

```python
# src/backend/config/settings.py (NEW)

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    azure_ai_project_endpoint: str
    azure_ai_model_deployment_name: str = "gpt-4o"
    azure_search_endpoint: str = ""
    azure_sql_server: str = ""
    azure_sql_database: str = "WideWorldImporters"
    enable_instrumentation: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
```

Tests construct `Settings(azure_ai_project_endpoint="fake", ...)` directly — no `monkeypatch.setenv` needed.

### Prompt Loading at Startup

Replace `_load_prompt()` calls in constructors with prompt strings passed as parameters:

```python
# Before (current): reads filesystem in __init__
class ParameterExtractorExecutor:
    def __init__(self, chat_client):
        self.agent = ChatAgent(instructions=_load_prompt(), ...)

# After: prompt loaded once at startup, injected as string
def create_param_extractor_agent(client: AzureAIClient, instructions: str) -> ChatAgent:
    return ChatAgent(name="parameter-extractor-agent", instructions=instructions, chat_client=client)
```

Agent factory functions in `agent.py` accept the prompt string. The `PipelineClients` factory loads all prompts once. Tests pass hardcoded strings.

### DataAssistant Rename (ConversationOrchestrator → DataAssistant)

The current `ConversationOrchestrator` name implies complex multi-agent coordination, but the class is actually a **data-focused assistant** that:

1. Manages conversation state (context, refinement history)
2. Classifies user intent via a single LLM call
3. Delegates to `process_query()` for data queries
4. Renders responses for the frontend

Rename to `DataAssistant` to accurately reflect this role. The name also accommodates future growth — if streaming data or unstructured data sources are added, they become additional capabilities of the assistant, not a fundamentally different pattern. The constructor accepts an injected `ChatAgent` (instead of creating one internally) for testability:

```python
class DataAssistant:
    def __init__(self, agent: ChatAgent, conversation_id: str | None = None) -> None:
        self.agent = agent
        self.context = ConversationContext()
        self._initial_conversation_id = conversation_id
```

Tests construct `DataAssistant(mock_agent)` — no `AzureAIClient` needed.

### Session Continuity Implementation Note

Current implementation anchors continuity to provider conversation IDs:

- `chat.py` reuses inbound `conversation_id` or pre-creates one from the provider on first turn.
- `DataAssistant` uses that value as `service_session_id` when calling `agent.get_session(...)`.
- Downstream pipeline LLM calls reuse the same provider conversation ID for trace/thread continuity.
- SSE responses return this provider `conversation_id` to the client each turn.

### Testability Summary

| Current Pattern | New Pattern | Test Impact |
|---|---|---|
| `sys.modules["agent_framework"] = MagicMock()` | Protocol injection via `PipelineClients` | Zero import hacks |
| `@patch("entities.shared.tools.search.search_query_templates")` | Pass `TemplateSearchService` fake | No `mock.patch` |
| `try: from api.step_events import ...; except ImportError` | Accept `ProgressReporter` parameter | Explicit, testable |
| `os.getenv("AZURE_SQL_SERVER")` scattered | `Settings` model passed through | Construct in test |
| `_load_prompt()` reads filesystem in `__init__` | Prompt string passed to factory | No filesystem dependency |
| `ConversationOrchestrator(client, conversation_id)` | `DataAssistant(agent, conversation_id)` | Mock only ChatAgent |
| `ALLOWED_TABLES = _load_allowed_tables()` at import | `allowed_tables` parameter on function | No import-time I/O |

## Project Structure

### Documentation (this feature)

```text
specs/003-simplify-workflow/
├── spec.md              # Feature specification
├── plan.md              # This file
└── tasks.md             # Task breakdown
```

### Source Code (repository root)

```text
src/backend/
├── api/
│   ├── routers/
│   │   └── chat.py                       # MODIFIED - call process_query() directly, use DataAssistant
│   ├── workflow_cache.py                  # MODIFIED or REMOVED - paused workflow storage changes
│   └── step_events.py                    # SIMPLIFIED - QueueReporter wraps existing logic
├── config/
│   └── settings.py                       # NEW - Pydantic Settings model (centralized env vars)
├── entities/
│   ├── assistant/                        # RENAMED from orchestrator/
│   │   ├── assistant.py                  # RENAMED from orchestrator.py - DataAssistant class
│   │   └── assistant_prompt.md           # RENAMED from orchestrator_prompt.md
│   ├── nl2sql_controller/
│   │   ├── executor.py                   # REMOVED - replaced by pipeline.py
│   │   └── pipeline.py                   # NEW - process_query() top-level function
│   ├── parameter_extractor/
│   │   ├── agent.py                      # MODIFIED - accept prompt string parameter
│   │   ├── executor.py                   # REMOVED - replaced by extractor.py
│   │   ├── extractor.py                  # NEW - extract_parameters() async function
│   │   ├── prompt.md                     # UNCHANGED
│   │   └── tools/                        # UNCHANGED
│   ├── parameter_validator/
│   │   ├── executor.py                   # REMOVED - replaced by validator.py
│   │   └── validator.py                  # NEW - validate_parameters() pure function
│   ├── query_builder/
│   │   ├── agent.py                      # MODIFIED - accept prompt string parameter
│   │   ├── executor.py                   # REMOVED - replaced by builder.py
│   │   ├── builder.py                    # NEW - build_query() async function
│   │   ├── prompt.md                     # UNCHANGED
│   │   └── tools/                        # UNCHANGED
│   ├── query_validator/
│   │   ├── executor.py                   # REMOVED - replaced by validator.py
│   │   └── validator.py                  # NEW - validate_query() pure function
│   ├── shared/
│   │   ├── protocols.py                  # NEW - Protocol interfaces (TemplateSearchService, SqlExecutor, ProgressReporter)
│   │   └── ...                           # UNCHANGED - search_client, column_filter, etc.
│   └── workflow/
│       ├── __init__.py                   # MODIFIED - export process_query instead of create_nl2sql_workflow
│       └── workflow.py                   # REMOVED - WorkflowBuilder graph replaced by pipeline.py
├── models/
│   ├── generation.py                     # MODIFIED - remove SQLDraftMessage wrapper if unused
│   └── extraction.py                     # MODIFIED - remove ExtractionRequestMessage if unused
└── config/                               # MODIFIED - add settings.py

tests/
├── conftest.py                           # MODIFIED - shared fixtures for protocol fakes, Settings
├── unit/
│   ├── test_validate_parameters.py       # NEW or MODIFIED - test pure function directly
│   ├── test_validate_query.py            # NEW or MODIFIED - test pure function directly
│   ├── test_process_query.py             # NEW - pipeline integration test with injected fakes
│   ├── test_data_assistant.py             # NEW - DataAssistant with mocked ChatAgent
│   ├── test_sse_endpoint.py              # NEW - SSE streaming endpoint with injected pipeline
│   └── ...                               # MODIFIED - update imports
└── integration/
    └── test_workflow_integration.py       # MODIFIED - test process_query() instead of workflow
```

**Structure Decision**: The existing `entities/` folder structure is preserved. Each entity folder keeps its identity but swaps `executor.py` (Executor subclass) for a module with plain functions. `agent.py` files are untouched — they create ChatAgent instances.

## Implementation Phases

### Phase 1: Define Protocols and Extract Deterministic Validators (US1 partial)

**Goal**: Define Protocol interfaces for I/O boundaries, create the `Settings` model, and extract ParameterValidator and QueryValidator logic into standalone pure functions. These are the safest starting point because validators have no LLM dependency (no I/O, no state) and protocols establish the testability foundation all later phases depend on.

#### 1.0 Protocol interfaces and Settings

**File**: `src/backend/entities/shared/protocols.py` (NEW)

- Define `TemplateSearchService`, `TableSearchService`, `SqlExecutor`, and `ProgressReporter` protocols
- Define `NoOpReporter` implementation (default for tests)
- Define `QueueReporter` implementation (production — wraps existing `step_events.py` logic)
- All protocols are `@runtime_checkable` for defensive assertions in production code

**File**: `src/backend/config/settings.py` (NEW)

- Define `Settings(BaseSettings)` with all env vars currently scattered across modules
- Uses `pydantic-settings` for `.env` file loading
- Tests construct `Settings(azure_ai_project_endpoint="test", ...)` directly

#### 1.1 Parameter validator extraction

**File**: `src/backend/entities/parameter_validator/validator.py` (NEW)

- Extract all validation functions (`_validate_integer`, `_validate_string`, `_validate_date`, `_validate_enum`, `_validate_allowed_values`) from `executor.py` — these are already free functions
- Create `validate_parameters(sql_draft: SQLDraft, parameters: list[ParameterDefinition]) -> SQLDraft`:
  - Runs all parameter validations
  - Returns `SQLDraft` with `params_validated=True` on success
  - Returns `SQLDraft` with violations list on failure
- No `Executor`, `WorkflowContext`, `@handler` references
- All existing validation logic preserved exactly

**File**: `src/backend/entities/parameter_validator/executor.py` (REMOVED after Phase 2 integration)

#### 1.2 Query validator extraction

**File**: `src/backend/entities/query_validator/validator.py` (NEW)

- Extract validation logic from `executor.py`: `SQL_INJECTION_PATTERNS` and all check functions
- **Remove module-level `ALLOWED_TABLES = _load_allowed_tables()` side effect** — the allowed tables set is loaded once at startup by `PipelineClients` and passed as a parameter
- Create `validate_query(sql_draft: SQLDraft, allowed_tables: set[str]) -> SQLDraft`:
  - Runs syntax checks, table allowlist, injection patterns, statement type verification
  - Returns `SQLDraft` with `query_validated=True` on success
  - Returns `SQLDraft` with violations list on failure
- No `Executor`, `WorkflowContext`, `@handler` references
- No filesystem I/O at import time

**File**: `src/backend/entities/query_validator/executor.py` (REMOVED after Phase 2 integration)

#### 1.3 Tests for protocols and extracted validators

**File**: `tests/conftest.py` (MODIFIED)

- Add shared fixtures: `FakeTemplateSearch`, `FakeSqlExecutor`, `NoOpReporter`, `test_settings`
- These fixtures are reused by all test files — no per-file boilerplate

**File**: `tests/unit/test_validate_parameters.py` (NEW or adapt existing)
**File**: `tests/unit/test_validate_query.py` (NEW or adapt existing)

- Call extracted functions directly with test `SQLDraft` instances
- Pass `allowed_tables={"Sales.Orders", "Application.People"}` as parameter — no config file needed
- No WorkflowContext mocks, no `sys.modules` hacks
- Verify identical behavior to current executor tests

---

### Phase 2: Extract LLM-Calling Functions (US1 partial — async functions with ChatAgent)

**Goal**: Extract ParameterExtractor and QueryBuilder logic into async functions that take a ChatAgent and return results directly. Agent factories accept prompt strings as parameters (no filesystem reads at init time). All progress reporting goes through the `ProgressReporter` protocol.

#### 2.1 Parameter extractor extraction

**File**: `src/backend/entities/parameter_extractor/extractor.py` (NEW)

- Create `extract_parameters(request: ParameterExtractionRequest, agent: ChatAgent, reporter: ProgressReporter = NoOpReporter()) -> SQLDraft | ClarificationRequest`:
  - Preserves deterministic fast-path (fuzzy matching)
  - Calls `agent.run()` for ambiguous cases (same as today)
  - Emits step events via `reporter.step_start()` / `reporter.step_end()` — no `try/except ImportError`
  - Returns `SQLDraft` (success) or `ClarificationRequest` (missing params)
- Import and use `agent.py` for ChatAgent creation (unchanged)
- Move helpers like `_resolve_with_fuzzy_match()`, `_build_extraction_prompt()` into this module
- Remove duplicate `get_request_user_id()` wrapper — user ID passed as parameter when needed

**File**: `src/backend/entities/parameter_extractor/agent.py` (MODIFIED)

- Factory function accepts prompt string: `create_param_extractor_agent(client: AzureAIClient, instructions: str) -> ChatAgent`
- No `_load_prompt()` call inside the factory — prompt loaded once at startup by `PipelineClients`

#### 2.2 Query builder extraction

**File**: `src/backend/entities/query_builder/builder.py` (NEW)

- Create `build_query(request: QueryBuilderRequest, agent: ChatAgent, reporter: ProgressReporter = NoOpReporter()) -> SQLDraft`:
  - Calls `agent.run()` for SQL generation (same as today)
  - Emits step events via `reporter` — no `try/except ImportError`
  - Returns `SQLDraft` with generated SQL
- Move helpers like `_build_generation_prompt()`, `_parse_query_response()` into this module

**File**: `src/backend/entities/query_builder/agent.py` (MODIFIED)

- Same pattern: `create_query_builder_agent(client: AzureAIClient, instructions: str) -> ChatAgent`

#### 2.3 Tests for extracted functions

- Call `extract_parameters()` and `build_query()` directly with mocked `ChatAgent`
- Pass `NoOpReporter()` (default) — no step event infrastructure needed
- Pass test prompt strings — no filesystem dependency
- No WorkflowContext mocks, no `sys.modules` hacks

---

### Phase 3: Build process_query() Pipeline (US1 + US2 — the central function)

**Goal**: Create the top-level `process_query()` function that replaces NL2SQLController + WorkflowBuilder graph.

#### 3.1 Pipeline function

**File**: `src/backend/entities/nl2sql_controller/pipeline.py` (NEW)

- Create `process_query(request: NL2SQLRequest, clients: PipelineClients) -> NL2SQLResponse | ClarificationRequest`:
  - `PipelineClients` is a frozen dataclass holding all dependencies — agents, services, and reporter:

    ```python
    @dataclass(frozen=True)
    class PipelineClients:
        param_extractor_agent: ChatAgent
        query_builder_agent: ChatAgent
        allowed_values_provider: AllowedValuesProvider
        template_search: TemplateSearchService    # Protocol — injectable
        table_search: TableSearchService          # Protocol — injectable
        sql_executor: SqlExecutor                 # Protocol — injectable
        reporter: ProgressReporter                # Protocol — defaults to NoOpReporter
        allowed_tables: set[str]                  # Loaded once at startup
    ```

  - Pipeline logic (extracted from NL2SQLController's handlers):
    1. `clients.template_search.search()` — no direct `@tool` call
    2. Score confidence → route:
       - High confidence match → `extract_parameters()` → `validate_parameters()` → `validate_query()` → `clients.sql_executor.execute()`
       - No match → `build_query()` → `validate_query()` → `clients.sql_executor.execute()`
    3. Handle ClarificationRequest returns (early exit)
    4. Apply column refinement (existing `refine_columns()`)
    5. Return `NL2SQLResponse`
  - All step events emitted via `clients.reporter` (explicit, no global state)
  - All routing is plain `if/else` — no message graph
  - **Fully testable**: construct `PipelineClients` with fakes for every I/O boundary

#### 3.2 Update workflow module

**File**: `src/backend/entities/workflow/__init__.py` (MODIFIED)

- Export `process_query` and `PipelineClients` instead of `create_nl2sql_workflow`

**File**: `src/backend/entities/workflow/workflow.py` (REMOVED)

- WorkflowBuilder graph no longer needed

#### 3.3 Client initialization

**File**: `src/backend/entities/workflow/clients.py` (NEW)

- `create_pipeline_clients(settings: Settings) -> PipelineClients`:
  - Creates `AzureAIClient` instances
  - **Loads prompt files once** from disk (not per-request)
  - Creates `ChatAgent` instances via updated `agent.py` factories (passing prompt strings)
  - Wraps `AzureSearchClient` in `TemplateSearchService` / `TableSearchService` adapters
  - Wraps `AzureSqlClient` in `SqlExecutor` adapter
  - Creates `QueueReporter` or `NoOpReporter` based on context
  - Loads `allowed_tables` from config file once
- Production calls this once at startup. Tests never call it — they construct `PipelineClients` directly with fakes.
- No `global` singletons — the returned `PipelineClients` is the single source of truth

---

### Phase 4: Rewire SSE Streaming and Rename DataAssistant (US2 + US3)

**Goal**: chat.py calls `process_query()` directly instead of `workflow.run_stream()`. Rename `ConversationOrchestrator` → `DataAssistant` and move to `entities/assistant/`.

#### 4.1 Simplify main streaming function

**File**: `src/backend/api/routers/chat.py` (MODIFIED)

- `generate_orchestrator_streaming_response()`:
  - Replace `workflow.run_stream(nl2sql_request)` loop with:

    ```python
    result = await process_query(nl2sql_request, clients)
    ```

  - Step events handled by `clients.reporter` (QueueReporter writes to the SSE queue)
  - Handle `ClarificationRequest` return by emitting clarification SSE data
  - Handle `NL2SQLResponse` return by calling `assistant.render_response()`
  - Remove all `WorkflowOutputEvent`, `ExecutorInvokedEvent`, `ExecutorCompletedEvent`, `WorkflowStatusEvent`, `RequestInfoEvent` handling
  - Use `DataAssistant` instead of `ConversationOrchestrator`

#### 4.2 Rename ConversationOrchestrator → DataAssistant

**File**: `src/backend/entities/assistant/assistant.py` (NEW — moved from `orchestrator/orchestrator.py`)

- Rename class `ConversationOrchestrator` → `DataAssistant`
- Accept `agent: ChatAgent` as constructor parameter (instead of creating it internally from `AzureAIClient`)
- Remove `_load_prompt()` from `__init__` — prompt loaded externally
- Keep all business logic: `classify_intent()`, `build_nl2sql_request()`, `update_context()`, `enrich_response()`, `render_response()`
- `ConversationContext` and `ClassificationResult` dataclasses stay unchanged

**File**: `src/backend/entities/assistant/assistant_prompt.md` (moved from `orchestrator/orchestrator_prompt.md`)

**File**: `src/backend/api/session_manager.py` (MODIFIED)

- Update imports and cache references from `ConversationOrchestrator` → `DataAssistant`

#### 4.3 Simplify clarification flow

#### 4.3 Simplify clarification flow

**File**: `src/backend/api/routers/chat.py` (MODIFIED)

- `generate_clarification_response_stream()`:
  - Replace `workflow.send_responses_streaming()` with calling `process_query()` passing the pending clarification context
  - Or: store the pending `SQLDraft` + template context (not the whole Workflow) and re-invoke the extraction step
  - `workflow_cache.py` becomes `clarification_cache.py` — stores pending extraction context, not a Workflow object

#### 4.4 Remove MAF event imports

- Remove imports: `ExecutorCompletedEvent`, `ExecutorInvokedEvent`, `RequestInfoEvent`, `WorkflowOutputEvent`, `WorkflowRunState`, `WorkflowStatusEvent`, `Workflow`
- Remove `TYPE_CHECKING` block for `Workflow`
- Remove old `orchestrator/` directory after confirming `assistant/` works

---

### Phase 5: Cleanup — Remove Dead Code and Wrapper Types (US4)

**Goal**: Remove all Executor classes, message wrapper types, and unused MAF imports.

#### 5.1 Remove executor files

- Delete `src/backend/entities/nl2sql_controller/executor.py`
- Delete `src/backend/entities/parameter_extractor/executor.py`
- Delete `src/backend/entities/parameter_validator/executor.py`
- Delete `src/backend/entities/query_builder/executor.py`
- Delete `src/backend/entities/query_validator/executor.py`
- Delete `src/backend/entities/workflow/workflow.py`
- Delete `src/backend/entities/orchestrator/` directory (replaced by `assistant/`)

#### 5.2 Remove/simplify message wrapper types

**File**: `src/backend/models/generation.py` (MODIFIED)

- Remove `SQLDraftMessage` if no longer used (was `source` field for message routing)
- Remove `QueryBuilderRequestMessage` if no longer used

**File**: `src/backend/models/extraction.py` (MODIFIED)

- Remove `ExtractionRequestMessage` if no longer used
- Remove `ClarificationMessage` if no longer used (keep `ClarificationRequest`)

**File**: `src/backend/models/__init__.py` (MODIFIED)

- Remove re-exports of deleted types

#### 5.3 Final import audit

- `grep -r "from agent_framework import.*Executor\|WorkflowBuilder\|WorkflowContext\|handler\|response_handler" src/backend/` must return zero results
- `grep -r "Executor\|WorkflowContext" src/backend/` should return zero results (except comments)
- `grep -r "ConversationOrchestrator" src/backend/` should return zero results (renamed to DataAssistant)
- `grep -r "try:.*from api.step_events" src/backend/entities/` should return zero results (replaced by ProgressReporter)
- `grep -r "os\.getenv" src/backend/entities/` should return zero results (replaced by Settings)

---

### Phase 6: Update Tests (US5)

**Goal**: All tests pass with the new function-based architecture.

#### 6.1 Update unit tests

- Update imports in all existing test files to use new function paths
- Remove any WorkflowContext mocks
- **Remove all `sys.modules` hacks** — no `sys.modules.setdefault("agent_framework", ...)` boilerplate
- **Remove all `importlib.util.spec_from_file_location` hacks** — import modules normally
- Tests for validators call `validate_parameters()` / `validate_query()` directly
- Tests for extractors/builders call `extract_parameters()` / `build_query()` with mocked ChatAgent

#### 6.2 New pipeline integration test

**File**: `tests/unit/test_process_query.py` (NEW)

- Test `process_query()` end-to-end with `PipelineClients` constructed from fakes:
  - `FakeTemplateSearch` returns canned `QueryTemplate` results
  - `FakeSqlExecutor` returns canned row data
  - `SpyReporter` captures step events for assertion
  - Mocked `ChatAgent` for LLM-calling paths
- Verify template-match path, dynamic-query path, clarification path, error path
- **No Azure credentials, no network, no filesystem** — runs in CI without env vars

#### 6.3 New DataAssistant test

**File**: `tests/unit/test_data_assistant.py` (NEW)

- Test `DataAssistant` with a mocked `ChatAgent` injected via constructor
- Test `classify_intent()` by controlling `agent.run()` return values
- Test `build_nl2sql_request()` with various `ConversationContext` states
- Test `update_context()` and `enrich_response()` (these are pure logic — no mocks needed)
- Test `render_response()` output format

#### 6.4 New SSE endpoint test

**File**: `tests/unit/test_sse_endpoint.py` (NEW)

- Test the chat streaming endpoint using `httpx.AsyncClient` with the FastAPI test client
- Inject `PipelineClients` with fakes into the app (via dependency override or factory)
- Verify SSE event structure: event names, field names, ordering
- Verify clarification flow produces correct SSE events
- **This test is only feasible because `process_query()` accepts injected services** — the key unlock from Protocol-based DI

#### 6.5 Update integration tests

**File**: `tests/integration/test_workflow_integration.py` (MODIFIED)

- Test `process_query()` instead of `Workflow.run_stream()`
- Simpler setup — construct `PipelineClients` with fakes, no WorkflowBuilder

---

### Phase 7: Quality Gates and Polish

**Goal**: All checks pass, documentation updated, testability verified.

- [ ] `uv run poe check` passes (lint + typecheck + test)
- [ ] `uv run poe metrics` shows reduced complexity
- [ ] Grep audit confirms zero Executor/Workflow/WorkflowContext imports
- [ ] Grep audit confirms zero `ConversationOrchestrator` references (renamed to `DataAssistant`)
- [ ] Grep audit confirms zero `try: from api.step_events` in entities/
- [ ] Grep audit confirms zero `os.getenv` in entities/
- [ ] Line count audit confirms ≥ 40% reduction
- [ ] SSE stream output manually verified against pre-refactor baseline
- [ ] No test file contains `sys.modules.setdefault("agent_framework"` or `importlib.util.spec_from_file_location`
- [ ] All new test files run without Azure credentials (CI-safe)

## Phase Dependencies

```
Phase 1 (Protocols + Validators) ──┐
                                    ├─► Phase 3 (process_query) ──► Phase 4 (SSE + Rename) ──► Phase 5 (Cleanup) ──► Phase 7 (Polish)
Phase 2 (LLM funcs) ──────────────┘                                                               ▲
                                                                                                   │
                                                                                         Phase 6 (Tests) ─────────┘
```

- **Phase 1 + Phase 2**: Independent, can run in parallel (different files). Phase 1 defines protocols used by all later phases.
- **Phase 3**: Depends on Phase 1 + Phase 2 (needs all extracted functions + protocols)
- **Phase 4**: Depends on Phase 3 (needs `process_query()` to exist). Includes DataAssistant rename.
- **Phase 5**: Depends on Phase 4 (old code removed only after new code is wired in)
- **Phase 6**: Starts during Phase 1 (test alongside extraction) but finishes after Phase 5
- **Phase 7**: Depends on all phases
- **Phase 6**: Starts during Phase 1 (test alongside extraction) but finishes after Phase 5
- **Phase 7**: Depends on all phases

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Clarification flow breaks (workflow pause/resume is framework-managed today) | Medium | High | Implement clarification as stored context + re-invocation in Phase 4.3. Test thoroughly. |
| Step event ordering changes | Low | Medium | Keep same queue mechanism via `QueueReporter`. Add SSE snapshot test comparing before/after. |
| ChatAgent behavior changes when called outside Executor | Low | Low | ChatAgent is independent of Executor — already proven by DataAssistant pattern. |
| Test coverage gaps from moved code | Medium | Low | Extract functions first, verify tests pass, then remove executor. Never both at once. |
| Concurrent requests break with shared state | Low | Medium | `process_query()` is stateless per-call (same as current fresh Workflow per request). `PipelineClients` is frozen/immutable. |
| Protocol interface drift from concrete implementations | Low | Low | `@runtime_checkable` protocols + type checker catch mismatches. Integration tests verify real adapters. |
| `pydantic-settings` adds a new dependency | Low | Low | Tiny, well-maintained dependency. Already compatible with existing Pydantic usage. |
| Rename causes import breakage across many files | Medium | Low | Phase 4.2 handles rename atomically. Grep audit in Phase 5.3 catches stragglers. |

## Complexity Tracking

No constitution violations. No complexity justifications needed. This refactor *reduces* complexity.
