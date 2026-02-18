# Implementation Plan: Simplify NL2SQL Workflow

**Branch**: `003-simplify-workflow` | **Date**: 2026-02-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-simplify-workflow/spec.md`

## Summary

Replace the MAF Executor/Workflow/WorkflowBuilder orchestration layer with plain async Python functions while keeping `ChatAgent`, `AzureAIClient`, `AgentThread`, and `@tool` for LLM calls and Foundry integration. The NL2SQL pipeline becomes a single `process_query()` async function that calls extracted sub-functions directly instead of routing messages through a WorkflowContext graph.

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

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I. Async-First | ✅ Pass | Extracted functions remain `async def` for I/O-bound operations. Pure validators become sync. |
| II. Validated Data at Boundaries | ✅ Pass | All Pydantic models (`NL2SQLResponse`, `SQLDraft`, `ClarificationRequest`) are unchanged. |
| III. Fully Typed | ✅ Pass | Function signatures gain explicit parameter/return types — improvement over `WorkflowContext` `Any` typing. |
| IV. Single-Responsibility Executors | ✅ Pass | Each function keeps single responsibility — but as a function, not a class. Simpler, same principle. |
| V. Automated Quality Gates | ✅ Pass | `uv run poe check` must pass at every phase checkpoint. |

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
│   │   └── chat.py                       # MODIFIED - remove workflow.run_stream(), call process_query() directly
│   ├── workflow_cache.py                  # MODIFIED or REMOVED - paused workflow storage changes
│   └── step_events.py                    # UNCHANGED - asyncio.Queue mechanism stays
├── entities/
│   ├── orchestrator/
│   │   └── orchestrator.py               # MINIMAL CHANGE - invoke process_query() instead of workflow
│   ├── nl2sql_controller/
│   │   ├── executor.py                   # REMOVED - replaced by pipeline.py
│   │   └── pipeline.py                   # NEW - process_query() top-level function
│   ├── parameter_extractor/
│   │   ├── agent.py                      # UNCHANGED - ChatAgent factory
│   │   ├── executor.py                   # REMOVED - replaced by extractor.py
│   │   ├── extractor.py                  # NEW - extract_parameters() async function
│   │   ├── prompt.md                     # UNCHANGED
│   │   └── tools/                        # UNCHANGED
│   ├── parameter_validator/
│   │   ├── executor.py                   # REMOVED - replaced by validator.py
│   │   └── validator.py                  # NEW - validate_parameters() pure function
│   ├── query_builder/
│   │   ├── agent.py                      # UNCHANGED - ChatAgent factory
│   │   ├── executor.py                   # REMOVED - replaced by builder.py
│   │   ├── builder.py                    # NEW - build_query() async function
│   │   ├── prompt.md                     # UNCHANGED
│   │   └── tools/                        # UNCHANGED
│   ├── query_validator/
│   │   ├── executor.py                   # REMOVED - replaced by validator.py
│   │   └── validator.py                  # NEW - validate_query() pure function
│   ├── shared/                           # UNCHANGED - search_client, column_filter, etc.
│   └── workflow/
│       ├── __init__.py                   # MODIFIED - export process_query instead of create_nl2sql_workflow
│       └── workflow.py                   # REMOVED - WorkflowBuilder graph replaced by pipeline.py
├── models/
│   ├── generation.py                     # MODIFIED - remove SQLDraftMessage wrapper if unused
│   └── extraction.py                     # MODIFIED - remove ExtractionRequestMessage if unused
└── config/                               # UNCHANGED

tests/
├── unit/
│   ├── test_validate_parameters.py       # NEW or MODIFIED - test pure function directly
│   ├── test_validate_query.py            # NEW or MODIFIED - test pure function directly
│   ├── test_process_query.py             # NEW - integration test for pipeline
│   └── ...                               # MODIFIED - update imports
└── integration/
    └── test_workflow_integration.py       # MODIFIED - test process_query() instead of workflow
```

**Structure Decision**: The existing `entities/` folder structure is preserved. Each entity folder keeps its identity but swaps `executor.py` (Executor subclass) for a module with plain functions. `agent.py` files are untouched — they create ChatAgent instances.

## Implementation Phases

### Phase 1: Extract Deterministic Validators (US1 partial — pure functions)

**Goal**: Extract ParameterValidator and QueryValidator logic into standalone pure functions. These are the safest starting point because they have no LLM dependency, no I/O, and no state.

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

- Extract validation logic from `executor.py`: `_load_allowed_tables()`, `ALLOWED_TABLES`, `SQL_INJECTION_PATTERNS`, and all check functions
- Create `validate_query(sql_draft: SQLDraft, allowed_tables: set[str]) -> SQLDraft`:
  - Runs syntax checks, table allowlist, injection patterns, statement type verification
  - Returns `SQLDraft` with `query_validated=True` on success
  - Returns `SQLDraft` with violations list on failure
- No `Executor`, `WorkflowContext`, `@handler` references

**File**: `src/backend/entities/query_validator/executor.py` (REMOVED after Phase 2 integration)

#### 1.3 Tests for extracted validators

**File**: `tests/unit/test_validate_parameters.py` (NEW or adapt existing)
**File**: `tests/unit/test_validate_query.py` (NEW or adapt existing)

- Call extracted functions directly with test `SQLDraft` instances
- No WorkflowContext mocks needed
- Verify identical behavior to current executor tests

---

### Phase 2: Extract LLM-Calling Functions (US1 partial — async functions with ChatAgent)

**Goal**: Extract ParameterExtractor and QueryBuilder logic into async functions that take a ChatAgent and return results directly.

#### 2.1 Parameter extractor extraction

**File**: `src/backend/entities/parameter_extractor/extractor.py` (NEW)

- Create `extract_parameters(request: ParameterExtractionRequest, agent: ChatAgent, ...) -> SQLDraft | ClarificationRequest`:
  - Preserves deterministic fast-path (fuzzy matching)
  - Calls `agent.run()` for ambiguous cases (same as today)
  - Accepts optional `asyncio.Queue` for step events
  - Returns `SQLDraft` (success) or `ClarificationRequest` (missing params)
- Import and use `agent.py` for ChatAgent creation (unchanged)
- Move helpers like `_resolve_with_fuzzy_match()`, `_build_extraction_prompt()` into this module

#### 2.2 Query builder extraction

**File**: `src/backend/entities/query_builder/builder.py` (NEW)

- Create `build_query(request: QueryBuilderRequest, agent: ChatAgent, ...) -> SQLDraft`:
  - Calls `agent.run()` for SQL generation (same as today)
  - Accepts optional `asyncio.Queue` for step events
  - Returns `SQLDraft` with generated SQL
- Move helpers like `_build_generation_prompt()`, `_parse_query_response()` into this module

#### 2.3 Tests for extracted functions

- Update/create tests that call `extract_parameters()` and `build_query()` directly with mocked `ChatAgent`
- No WorkflowContext mocks needed

---

### Phase 3: Build process_query() Pipeline (US1 + US2 — the central function)

**Goal**: Create the top-level `process_query()` function that replaces NL2SQLController + WorkflowBuilder graph.

#### 3.1 Pipeline function

**File**: `src/backend/entities/nl2sql_controller/pipeline.py` (NEW)

- Create `process_query(request: NL2SQLRequest, clients: PipelineClients, step_queue: asyncio.Queue | None = None) -> NL2SQLResponse | ClarificationRequest`:
  - `PipelineClients` is a simple dataclass holding `param_extractor_agent`, `query_builder_agent`, `allowed_values_provider`
  - Pipeline logic (extracted from NL2SQLController's handlers):
    1. Search templates via `search_query_templates()` tool
    2. Score confidence → route:
       - High confidence match → `extract_parameters()` → `validate_parameters()` → `validate_query()` → execute SQL
       - No match → `build_query()` → `validate_query()` → execute SQL
    3. Handle ClarificationRequest returns (early exit)
    4. Apply column refinement (existing `refine_columns()`)
    5. Return `NL2SQLResponse`
  - All routing is plain `if/else` — no message graph
  - Step events emitted via `step_queue.put_nowait()` (same mechanism)

#### 3.2 Update workflow module

**File**: `src/backend/entities/workflow/__init__.py` (MODIFIED)

- Export `process_query` and `PipelineClients` instead of `create_nl2sql_workflow`

**File**: `src/backend/entities/workflow/workflow.py` (REMOVED)

- WorkflowBuilder graph no longer needed

#### 3.3 Client initialization

**File**: `src/backend/entities/workflow/clients.py` (NEW)

- Move `_get_clients()` and `_get_allowed_values_provider()` singletons here
- Create `get_pipeline_clients() -> PipelineClients` — constructs the clients dataclass
- No Workflow or Executor references

---

### Phase 4: Rewire SSE Streaming (US2 + US3)

**Goal**: chat.py calls `process_query()` directly instead of `workflow.run_stream()`.

#### 4.1 Simplify main streaming function

**File**: `src/backend/api/routers/chat.py` (MODIFIED)

- `generate_orchestrator_streaming_response()`:
  - Replace `workflow.run_stream(nl2sql_request)` loop with:
    ```python
    result = await process_query(nl2sql_request, clients, step_queue)
    ```
  - Drain step_queue after `process_query()` returns (same pattern, simpler)
  - Handle `ClarificationRequest` return by emitting clarification SSE data
  - Handle `NL2SQLResponse` return by calling `orchestrator.render_response()`
  - Remove all `WorkflowOutputEvent`, `ExecutorInvokedEvent`, `ExecutorCompletedEvent`, `WorkflowStatusEvent`, `RequestInfoEvent` handling

#### 4.2 Simplify clarification flow

**File**: `src/backend/api/routers/chat.py` (MODIFIED)

- `generate_clarification_response_stream()`:
  - Replace `workflow.send_responses_streaming()` with calling `process_query()` passing the pending clarification context
  - Or: store the pending `SQLDraft` + template context (not the whole Workflow) and re-invoke the extraction step
  - `workflow_cache.py` becomes `clarification_cache.py` — stores pending extraction context, not a Workflow object

#### 4.3 Remove MAF event imports

- Remove imports: `ExecutorCompletedEvent`, `ExecutorInvokedEvent`, `RequestInfoEvent`, `WorkflowOutputEvent`, `WorkflowRunState`, `WorkflowStatusEvent`, `Workflow`
- Remove `TYPE_CHECKING` block for `Workflow`

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

---

### Phase 6: Update Tests (US5)

**Goal**: All tests pass with the new function-based architecture.

#### 6.1 Update unit tests

- Update imports in all existing test files to use new function paths
- Remove any WorkflowContext mocks
- Tests for validators call `validate_parameters()` / `validate_query()` directly
- Tests for extractors/builders call `extract_parameters()` / `build_query()` with mocked ChatAgent

#### 6.2 New pipeline integration test

**File**: `tests/unit/test_process_query.py` (NEW)

- Test `process_query()` end-to-end with mocked ChatAgent and tool responses
- Verify template-match path, dynamic-query path, clarification path, error path

#### 6.3 Update integration tests

**File**: `tests/integration/test_workflow_integration.py` (MODIFIED)

- Test `process_query()` instead of `Workflow.run_stream()`
- Simpler setup — no WorkflowBuilder, just create `PipelineClients`

---

### Phase 7: Quality Gates and Polish

**Goal**: All checks pass, documentation updated.

- [ ] `uv run poe check` passes (lint + typecheck + test)
- [ ] `uv run poe metrics` shows reduced complexity
- [ ] Grep audit confirms zero Executor/Workflow/WorkflowContext imports
- [ ] Line count audit confirms ≥ 40% reduction
- [ ] SSE stream output manually verified against pre-refactor baseline

## Phase Dependencies

```
Phase 1 (Validators) ──┐
                        ├─► Phase 3 (process_query) ──► Phase 4 (SSE) ──► Phase 5 (Cleanup) ──► Phase 7 (Polish)
Phase 2 (LLM funcs) ───┘                                                       ▲
                                                                                │
                                                                      Phase 6 (Tests) ─────────┘
```

- **Phase 1 + Phase 2**: Independent, can run in parallel (different files)
- **Phase 3**: Depends on Phase 1 + Phase 2 (needs all extracted functions)
- **Phase 4**: Depends on Phase 3 (needs `process_query()` to exist)
- **Phase 5**: Depends on Phase 4 (old code removed only after new code is wired in)
- **Phase 6**: Starts during Phase 1 (test alongside extraction) but finishes after Phase 5
- **Phase 7**: Depends on all phases

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Clarification flow breaks (workflow pause/resume is framework-managed today) | Medium | High | Implement clarification as stored context + re-invocation in Phase 4.2. Test thoroughly. |
| Step event ordering changes | Low | Medium | Keep same `asyncio.Queue` mechanism. Add SSE snapshot test comparing before/after. |
| ChatAgent behavior changes when called outside Executor | Low | Low | ChatAgent is independent of Executor — already proven by ConversationOrchestrator pattern. |
| Test coverage gaps from moved code | Medium | Low | Extract functions first, verify tests pass, then remove executor. Never both at once. |
| Concurrent requests break with shared state | Low | Medium | `process_query()` is stateless per-call (same as current fresh Workflow per request). |

## Complexity Tracking

No constitution violations. No complexity justifications needed. This refactor *reduces* complexity.
