# Tasks: Simplify NL2SQL Workflow

**Input**: Design documents from `/specs/003-simplify-workflow/`
**Prerequisites**: plan.md (required), spec.md (required for user stories)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

## Phase 1: Baseline Snapshot

**Purpose**: Capture pre-refactor measurements for success criteria validation

- [x] T001 Capture baseline SSE stream output for a template-based query and a dynamic query (save as fixtures in tests/fixtures/). Record line counts of all pipeline files. Run `uv run poe check` to confirm green baseline.

**Checkpoint**: Baseline recorded — all changes measured against this

---

## Phase 2: Define Protocols, Settings, and Extract Deterministic Validators (US1 + US6)

**Goal**: Define Protocol interfaces for I/O boundaries, create centralized Settings, and extract ParameterValidator and QueryValidator as pure functions callable without WorkflowContext.

**Independent Test**: Import `validate_parameters()` or `validate_query()` in a test, pass `SQLDraft` + config, verify output matches current behavior — no framework mocks, no `sys.modules` hacks needed.

### Protocols & Settings

- [x] T002 [P] [US6] Create `src/backend/entities/shared/protocols.py` with `TemplateSearchService`, `TableSearchService`, `SqlExecutor`, `ProgressReporter` protocols (all `@runtime_checkable`). Add `NoOpReporter` and `QueueReporter` implementations. `QueueReporter` wraps existing `step_events.py` logic.
- [x] T003 [P] [US6] Create `src/backend/config/settings.py` with a `Settings(BaseSettings)` Pydantic model centralizing all environment variables currently scattered across `workflow.py`, `chat.py`, `sql_client.py`, `search_client.py`. Add `pydantic-settings` to `pyproject.toml` if not present.
- [x] T004 [P] [US5+US6] Update `tests/conftest.py` with shared fixtures: `FakeTemplateSearch`, `FakeTableSearch`, `FakeSqlExecutor`, `SpyReporter`, `NoOpReporter`, `test_settings` (a `Settings` instance with test values). These fixtures eliminate per-file boilerplate.

### Validator Extraction

- [x] T005 [P] [US1] Extract parameter validation logic into `validate_parameters(sql_draft, parameters) -> SQLDraft` in src/backend/entities/parameter_validator/validator.py. Move `_validate_integer`, `_validate_string`, `_validate_date`, `_validate_enum`, `_validate_allowed_values`, and the orchestrating `@handler` body. No Executor/WorkflowContext references.
- [x] T006 [P] [US1] Extract query validation logic into `validate_query(sql_draft, allowed_tables) -> SQLDraft` in src/backend/entities/query_validator/validator.py. Move `SQL_INJECTION_PATTERNS` and all check functions. **Remove module-level `ALLOWED_TABLES = _load_allowed_tables()` side effect** — `allowed_tables` is a parameter. No import-time I/O.

### Tests

- [x] T007 [P] [US1+US5] Create/update unit tests calling `validate_parameters()` directly with test SQLDraft instances in tests/unit/test_validate_parameters.py. No `sys.modules` hacks. Verify identical behavior to current executor tests (type checks, range, regex, allowed values, edge cases).
- [x] T008 [P] [US1+US5] Create/update unit tests calling `validate_query()` directly with test SQLDraft instances in tests/unit/test_validate_query.py. Pass `allowed_tables={"Sales.Orders", ...}` as parameter — no config file needed. Verify table allowlist, injection patterns, syntax checks, statement type.

**Checkpoint**: Protocols defined, Settings model created, both validators callable as pure functions. No import-time side effects. Test fixtures shared via conftest.py.

---

## Phase 3: Extract LLM-Calling Functions (US1 — Async Functions with ChatAgent)

**Goal**: ParameterExtractor and QueryBuilder become async functions that take a ChatAgent and return results directly. Agent factories accept prompt strings (no filesystem reads at init). Progress emitted via `ProgressReporter` protocol.

**Independent Test**: Call `extract_parameters()` with mocked ChatAgent and `NoOpReporter()`, verify it returns SQLDraft or ClarificationRequest. No WorkflowContext, no `sys.modules` hacks.

### Implementation

- [x] T009 [US1] Extract parameter extraction logic into `extract_parameters(request, agent, reporter=NoOpReporter()) -> SQLDraft | ClarificationRequest` in src/backend/entities/parameter_extractor/extractor.py. Preserve deterministic fast-path (fuzzy matching) and LLM fallback. Use `reporter.step_start()`/`reporter.step_end()` — no `try/except ImportError`. Remove duplicate `get_request_user_id()` wrapper.
- [x] T010 [US1] Extract query builder logic into `build_query(request, agent, reporter=NoOpReporter()) -> SQLDraft` in src/backend/entities/query_builder/builder.py. Preserve LLM SQL generation. Use `reporter` for step events.
- [x] T011 [US1+US6] Modify `agent.py` factories to accept prompt string parameter: `create_param_extractor_agent(client, instructions: str) -> ChatAgent` and `create_query_builder_agent(client, instructions: str) -> ChatAgent`. No `_load_prompt()` in factory.

### Tests

- [x] T012 [P] [US1+US5] Create/update tests calling `extract_parameters()` with mocked ChatAgent and `NoOpReporter()` in tests/unit/test_extract_parameters.py. Test deterministic path, LLM path, clarification path. No filesystem needed (pass test prompt string).
- [x] T013 [P] [US1+US5] Create/update tests calling `build_query()` with mocked ChatAgent and `NoOpReporter()` in tests/unit/test_build_query.py. Test SQL generation and response parsing.

**Checkpoint**: All four sub-functions callable independently. All accept `ProgressReporter` for step events. Agent factories accept prompt strings. Executor files still present.

---

## Phase 4: Build process_query() Pipeline (US1 + US2 + US6)

**Goal**: Single `process_query()` function replaces NL2SQLController executor + WorkflowBuilder graph. Uses Protocol-injected services via `PipelineClients`.

**Independent Test**: Construct `PipelineClients` entirely from fakes (no Azure, no network, no filesystem). Call `process_query()`, verify NL2SQLResponse.

### Implementation

- [x] T014 [US1+US6] Create `PipelineClients` frozen dataclass in src/backend/entities/workflow/clients.py with fields: `param_extractor_agent`, `query_builder_agent`, `allowed_values_provider`, `template_search: TemplateSearchService`, `table_search: TableSearchService`, `sql_executor: SqlExecutor`, `reporter: ProgressReporter`, `allowed_tables: set[str]`.
- [x] T015 [US6] Create `create_pipeline_clients(settings: Settings) -> PipelineClients` factory in clients.py. Loads prompts once from disk, creates agents via updated factories, wraps Azure clients in Protocol adapters, loads `allowed_tables` from config file. No module-level singletons.
- [x] T016 [US1+US2] Create `process_query(request, clients) -> NL2SQLResponse | ClarificationRequest` in src/backend/entities/nl2sql_controller/pipeline.py. Routing via `clients.template_search.search()` (not direct `@tool` call) → (extract_parameters | build_query) → validate_parameters → validate_query → `clients.sql_executor.execute()` → refine columns. Step events via `clients.reporter`. All plain if/else.
- [x] T017 [US2] Update src/backend/entities/workflow/**init**.py to export `process_query` and `PipelineClients` instead of `create_nl2sql_workflow`.

### Tests

- [x] T018 [US1+US2+US5+US6] Create pipeline integration test in tests/unit/test_process_query.py. Construct `PipelineClients` with `FakeTemplateSearch`, `FakeSqlExecutor`, `SpyReporter`, mocked ChatAgent. Cover template-match path, dynamic-query path, clarification path, validation failure, error recovery. **No Azure credentials, no network, no filesystem** — CI-safe.

**Checkpoint**: `process_query()` works with injected fakes. Fully testable without Azure.

---

## Phase 5: Rewire SSE Streaming and Rename DataAssistant (US2 + US3 + US6)

**Goal**: chat.py calls `process_query()` directly. Rename `ConversationOrchestrator` → `DataAssistant` and move to `entities/assistant/`. No more `workflow.run_stream()` event loop.

**Independent Test**: Start dev API server, send a query, verify SSE stream has same events/order as baseline capture.

### Implementation

- [x] T019 [US6] Rename `ConversationOrchestrator` → `DataAssistant` in new file src/backend/entities/assistant/assistant.py (moved from orchestrator/orchestrator.py). Accept `agent: ChatAgent` as constructor parameter instead of `AzureAIClient`. Move prompt to src/backend/entities/assistant/assistant_prompt.md.
- [x] T020 [US2+US3] Rewrite `generate_orchestrator_streaming_response()` in src/backend/api/routers/chat.py: replace `workflow.run_stream()` with `result = await process_query(request, clients)`. Step events handled by `clients.reporter` (QueueReporter). Use `DataAssistant` instead of `ConversationOrchestrator`.
- [x] T021 [US2+US3] Rewrite `generate_clarification_response_stream()` in src/backend/api/routers/chat.py: replace `workflow.send_responses_streaming()` with recalled context + `process_query()`. Convert workflow_cache.py to store pending extraction context (SQLDraft + template + params) instead of a Workflow object.
- [x] T022 [US3] Remove MAF event imports from chat.py: `ExecutorCompletedEvent`, `ExecutorInvokedEvent`, `RequestInfoEvent`, `WorkflowOutputEvent`, `WorkflowRunState`, `WorkflowStatusEvent`. Remove `TYPE_CHECKING` block for `Workflow`.
- [x] T023 [US6] Update src/backend/api/session_manager.py imports and cache references from `ConversationOrchestrator` → `DataAssistant`.

### Tests

- [x] T024 [US5+US6] Create tests/unit/test_data_assistant.py: test `DataAssistant` with mocked ChatAgent injected via constructor. Test `classify_intent()`, `build_nl2sql_request()`, `update_context()`, `enrich_response()`, `render_response()`. No AzureAIClient needed.

**Checkpoint**: SSE streaming works via `process_query()`. DataAssistant is testable. Workflow is no longer invoked anywhere.

---

## Phase 6: Remove Dead Code and Wrapper Types (US4)

**Goal**: Delete all Executor classes, WorkflowBuilder, old orchestrator directory, and message wrapper types.

**Independent Test**: `grep -r "Executor\|WorkflowContext\|WorkflowBuilder\|ConversationOrchestrator" src/backend/` returns zero results (excluding comments).

### Implementation

- [x] T025 [US4] Delete executor files: src/backend/entities/nl2sql_controller/executor.py, src/backend/entities/parameter_extractor/executor.py, src/backend/entities/parameter_validator/executor.py, src/backend/entities/query_builder/executor.py, src/backend/entities/query_validator/executor.py.
- [x] T026 [US4] Delete src/backend/entities/workflow/workflow.py.
- [x] T027 [US4] Delete src/backend/entities/orchestrator/ directory (replaced by entities/assistant/).
- [x] T028 [US4] Remove message wrapper types: `SQLDraftMessage`, `ExtractionRequestMessage`, `QueryBuilderRequestMessage`, `ClarificationMessage` from src/backend/models/generation.py and src/backend/models/extraction.py. Update src/backend/models/**init**.py re-exports.
- [x] T029 [US4] Run import audit: `grep -r "from agent_framework import.*Executor\|WorkflowBuilder\|WorkflowContext\|handler\|response_handler" src/backend/` must return zero. `grep -r "ConversationOrchestrator" src/backend/` must return zero. `grep -r "try:.*from api.step_events" src/backend/entities/` must return zero. Fix any remaining references.

**Checkpoint**: All MAF orchestration code is removed. DataAssistant replaces old orchestrator. Only ChatAgent/AzureAIClient/AgentThread/@tool remain.

---

## Phase 7: Update All Tests (US5 + US6)

**Goal**: Full test suite passes with the new architecture. Zero `sys.modules` hacks. Tests run without Azure credentials.

### Implementation

- [x] T030 [US5] Update imports in all existing test files under tests/unit/ and tests/integration/ to use new function paths (validator.py, extractor.py, builder.py, pipeline.py, session.py). Remove any WorkflowContext, Executor, or `sys.modules` mocks.
- [x] T031 [US5] Update tests/integration/test_workflow_integration.py to test `process_query()` directly with `PipelineClients` constructed from fakes. Remove Workflow setup.
- [x] T032 [US5+US6] Create tests/unit/test_sse_endpoint.py: test the chat streaming endpoint using `httpx.AsyncClient` with FastAPI test client. Inject `PipelineClients` with fakes via dependency override. Verify SSE event structure, ordering, and clarification flow.
- [x] T033 [US5] Verify all tests pass: `uv run poe test`. Confirm no test file contains `sys.modules.setdefault("agent_framework"` or `importlib.util.spec_from_file_location`.

**Checkpoint**: `uv run poe test` exits with code 0. Zero test files reference Executor/Workflow/WorkflowContext. All tests CI-safe.

---

## Phase 8: Polish & Quality Gates

**Purpose**: Final validation across all user stories

- [ ] T034 Run `uv run poe check` — all quality gates pass (lint, typecheck, test)
- [ ] T035 Run `uv run poe metrics` — verify reduced complexity scores
- [ ] T036 Compare SSE stream output for template-based and dynamic queries against baseline (T001). Verify structure-identical.
- [ ] T037 Record final line counts of pipeline files. Calculate reduction vs baseline. Verify ≥ 40%.
- [ ] T038 Run import audit: confirm zero Executor/Workflow/WorkflowContext/handler/response_handler/ConversationOrchestrator imports in src/backend/. Confirm zero `try: from api.step_events` in entities/. Confirm zero `os.getenv` in entities/.
- [ ] T039 Verify no test file contains `sys.modules.setdefault("agent_framework"` or `importlib.util.spec_from_file_location`. Verify all test files run without Azure credentials.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Baseline (Phase 1)**: No dependencies — start immediately
- **Protocols + Validators (Phase 2)**: No dependencies — can start in parallel with Phase 1. Defines protocols used by all later phases.
- **LLM Functions (Phase 3)**: Depends on Phase 2 protocols (`ProgressReporter`). Can start agent.py changes in parallel.
- **Pipeline (Phase 4)**: Depends on Phase 2 + Phase 3 (needs all extracted functions + protocols)
- **SSE + Rename (Phase 5)**: Depends on Phase 4 (needs `process_query()`). Includes DataAssistant rename.
- **Dead Code (Phase 6)**: Depends on Phase 5 (old code removed only after new code is wired in)
- **Tests (Phase 7)**: Starts during Phase 2 (tests alongside extraction) but finishes after Phase 6
- **Polish (Phase 8)**: Depends on Phase 6 + Phase 7

### User Story Independence

```
Phase 2 (Protocols + Validators) ─┐
                                   ├──► Phase 4 (Pipeline) ──► Phase 5 (SSE + Rename) ──► Phase 6 (Cleanup) ──► Phase 8 (Polish)
Phase 3 (LLM Functions) ──────────┘                                                            ▲
                                                                                                │
                                                                               Phase 7 (Tests) ─┘
```

- **Phase 2 + Phase 3**: Phase 2 starts first (defines protocols). Phase 3 uses `ProgressReporter` from Phase 2.
- **Phase 4**: Merges both extraction streams into `process_query()` with `PipelineClients`
- **Phase 5**: Integrates pipeline into the API layer, renames DataAssistant
- **Phase 6 + Phase 7**: Sequential — remove old, then verify tests
- **Phase 8**: Final validation gate

### Within Each Phase

- Extract function(s) before writing tests for them
- New module before old module deletion
- Backend before frontend (though no frontend changes expected)
- Run `uv run poe check` at each checkpoint

### Parallel Opportunities

Within Phase 2: T002 (protocols) + T003 (settings) + T004 (fixtures) in parallel
Within Phase 2: T005 (param validator) + T006 (query validator) in parallel
Within Phase 2: T007 (param tests) + T008 (query tests) in parallel
Within Phase 3: T012 (extractor tests) + T013 (builder tests) in parallel after T009 + T010
Phase 2 validators and Phase 2 protocols can start concurrently

---

## Implementation Strategy

### MVP First (Phase 1–5)

1. Complete Phase 1: Baseline snapshot
2. Complete Phase 2 + Phase 3: Extract all functions (parallel)
3. Complete Phase 4: Build `process_query()` pipeline
4. Complete Phase 5: Rewire SSE streaming
5. **STOP and VALIDATE**: Run full app, verify SSE output matches baseline
6. This alone delivers the core value — simplified architecture

### Full Cleanup (Phase 6–8)

1. Complete Phase 6: Remove all dead code
2. Complete Phase 7: Final test updates
3. Complete Phase 8: Quality gates and measurements

### Task Count Summary

| Phase | Story | Tasks | Parallel |
|-------|-------|-------|----------|
| Baseline | — | 1 | — |
| Protocols + Validators | US1+US6 | 7 | 6 |
| LLM Functions | US1+US6 | 5 | 2 |
| Pipeline | US1+US2+US6 | 5 | 0 |
| SSE + Rename | US2+US3+US6 | 6 | 0 |
| Dead Code | US4 | 5 | 0 |
| Tests | US5+US6 | 4 | 0 |
| Polish | — | 6 | 3 |
| **Total** | | **39** | **11** |

---

## Notes

- `entities/nl2sql_controller/executor.py` (1,296 lines) is the largest file and most complex extraction — tackle in Phase 4 after all sub-functions exist
- `entities/parameter_validator/executor.py` and `entities/query_validator/executor.py` are 100% deterministic — safest to extract first
- ChatAgent instances continue to be created in `agent.py` files — no change to Foundry agent registration
- Agent factories now accept prompt strings instead of calling `_load_prompt()` — enables test construction without filesystem
- The `@tool` decorator on shared tools (`template_search`, `table_search`, `execute_sql`) is unchanged — these are independent of Executor/Workflow
- `process_query()` uses Protocol-injected services, not direct `@tool` call — `@tool` functions remain for ChatAgent tool registration only
- Step event emission moves from `ContextVar`-based `step_events.py` to injectable `ProgressReporter` protocol
- `ConversationOrchestrator` → `DataAssistant` rename happens in Phase 5 alongside SSE rewire (minimizes rename churn)
- Clarification flow is the riskiest change (currently uses workflow pause/resume via `RequestInfoEvent`) — Phase 5 T021 needs thorough testing
- Commit after each task or logical group; run `uv run poe check` at each checkpoint
