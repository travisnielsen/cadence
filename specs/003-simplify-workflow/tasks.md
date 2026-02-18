# Tasks: Simplify NL2SQL Workflow

**Input**: Design documents from `/specs/003-simplify-workflow/`
**Prerequisites**: plan.md (required), spec.md (required for user stories)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

## Phase 1: Baseline Snapshot

**Purpose**: Capture pre-refactor measurements for success criteria validation

- [ ] T001 Capture baseline SSE stream output for a template-based query and a dynamic query (save as fixtures in tests/fixtures/). Record line counts of all pipeline files. Run `uv run poe check` to confirm green baseline.

**Checkpoint**: Baseline recorded — all changes measured against this

---

## Phase 2: Extract Deterministic Validators (US1 — Pure Functions)

**Goal**: ParameterValidator and QueryValidator become standalone pure functions callable without WorkflowContext.

**Independent Test**: Import `validate_parameters()` or `validate_query()` in a test, pass `SQLDraft` + config, verify output matches current behavior — no framework mocks needed.

### Implementation

- [ ] T002 [P] [US1] Extract parameter validation logic into `validate_parameters(sql_draft, parameters) -> SQLDraft` in src/backend/entities/parameter_validator/validator.py. Move `_validate_integer`, `_validate_string`, `_validate_date`, `_validate_enum`, `_validate_allowed_values`, and the orchestrating `@handler` body. No Executor/WorkflowContext references.
- [ ] T003 [P] [US1] Extract query validation logic into `validate_query(sql_draft, allowed_tables) -> SQLDraft` in src/backend/entities/query_validator/validator.py. Move `_load_allowed_tables`, `ALLOWED_TABLES`, `SQL_INJECTION_PATTERNS`, and all check functions. No Executor/WorkflowContext references.

### Tests

- [ ] T004 [P] [US1+US5] Create/update unit tests calling `validate_parameters()` directly with test SQLDraft instances in tests/unit/test_validate_parameters.py. Verify identical behavior to current executor tests (type checks, range, regex, allowed values, edge cases).
- [ ] T005 [P] [US1+US5] Create/update unit tests calling `validate_query()` directly with test SQLDraft instances in tests/unit/test_validate_query.py. Verify table allowlist, injection patterns, syntax checks, statement type.

**Checkpoint**: Both validators are callable as pure functions. Old executor files still exist but are no longer the implementation path.

---

## Phase 3: Extract LLM-Calling Functions (US1 — Async Functions with ChatAgent)

**Goal**: ParameterExtractor and QueryBuilder become async functions that take a ChatAgent and return results directly.

**Independent Test**: Call `extract_parameters()` with mocked ChatAgent, verify it returns SQLDraft or ClarificationRequest. No WorkflowContext needed.

### Implementation

- [ ] T006 [US1] Extract parameter extraction logic into `extract_parameters(request, agent, step_queue?) -> SQLDraft | ClarificationRequest` in src/backend/entities/parameter_extractor/extractor.py. Preserve deterministic fast-path (fuzzy matching) and LLM fallback. Move helpers: `_resolve_with_fuzzy_match`, `_build_extraction_prompt`, `_parse_extraction_response`.
- [ ] T007 [US1] Extract query builder logic into `build_query(request, agent, step_queue?) -> SQLDraft` in src/backend/entities/query_builder/builder.py. Preserve LLM SQL generation. Move helpers: `_build_generation_prompt`, `_parse_query_response`.

### Tests

- [ ] T008 [P] [US1+US5] Create/update tests calling `extract_parameters()` with mocked ChatAgent in tests/unit/test_extract_parameters.py. Test deterministic path, LLM path, clarification path.
- [ ] T009 [P] [US1+US5] Create/update tests calling `build_query()` with mocked ChatAgent in tests/unit/test_build_query.py. Test SQL generation and response parsing.

**Checkpoint**: All four sub-functions (validate_parameters, validate_query, extract_parameters, build_query) are callable independently. Executor files still present.

---

## Phase 4: Build process_query() Pipeline (US1 + US2)

**Goal**: Single `process_query()` function replaces NL2SQLController executor + WorkflowBuilder graph. This is the core of the refactor.

**Independent Test**: Call `process_query()` with mocked agents, verify it returns NL2SQLResponse for template and dynamic paths.

### Implementation

- [ ] T010 [US1] Create `PipelineClients` dataclass and `get_pipeline_clients()` factory in src/backend/entities/workflow/clients.py. Move `_get_clients()` and `_get_allowed_values_provider()` singletons from workflow.py.
- [ ] T011 [US1+US2] Create `process_query(request, clients, step_queue?) -> NL2SQLResponse | ClarificationRequest` in src/backend/entities/nl2sql_controller/pipeline.py. Implement full routing logic: template search → (extract_parameters | build_query) → validate_parameters → validate_query → execute SQL → refine columns → return response. All plain if/else control flow.
- [ ] T012 [US2] Update src/backend/entities/workflow/__init__.py to export `process_query` and `PipelineClients` instead of `create_nl2sql_workflow`.

### Tests

- [ ] T013 [US1+US2+US5] Create pipeline integration test calling `process_query()` end-to-end with mocked ChatAgent and tool responses in tests/unit/test_process_query.py. Cover template-match path, dynamic-query path, clarification path, validation failure path, error recovery path.

**Checkpoint**: `process_query()` works and returns correct results. Workflow.run_stream() still exists but is no longer the only path.

---

## Phase 5: Rewire SSE Streaming (US2 + US3)

**Goal**: chat.py calls `process_query()` directly. No more `workflow.run_stream()` event loop.

**Independent Test**: Start dev API server, send a query, verify SSE stream has same events/order as baseline capture.

### Implementation

- [ ] T014 [US2+US3] Rewrite `generate_orchestrator_streaming_response()` in src/backend/api/routers/chat.py: replace `workflow.run_stream()` with `result = await process_query(request, clients, step_queue)`. Drain step_queue to SSE. Handle `NL2SQLResponse` vs `ClarificationRequest` return.
- [ ] T015 [US2+US3] Rewrite `generate_clarification_response_stream()` in src/backend/api/routers/chat.py: replace `workflow.send_responses_streaming()` with recalled context + `process_query()`. Convert workflow_cache.py to store pending extraction context (SQLDraft + template + params) instead of a Workflow object.
- [ ] T016 [US3] Remove MAF event imports from chat.py: `ExecutorCompletedEvent`, `ExecutorInvokedEvent`, `RequestInfoEvent`, `WorkflowOutputEvent`, `WorkflowRunState`, `WorkflowStatusEvent`. Remove `TYPE_CHECKING` block for `Workflow`.

**Checkpoint**: SSE streaming works via `process_query()`. Workflow is no longer invoked anywhere.

---

## Phase 6: Remove Dead Code and Wrapper Types (US4)

**Goal**: Delete all Executor classes, WorkflowBuilder, and message wrapper types.

**Independent Test**: `grep -r "Executor\|WorkflowContext\|WorkflowBuilder" src/backend/` returns zero results (excluding comments).

### Implementation

- [ ] T017 [US4] Delete executor files: src/backend/entities/nl2sql_controller/executor.py, src/backend/entities/parameter_extractor/executor.py, src/backend/entities/parameter_validator/executor.py, src/backend/entities/query_builder/executor.py, src/backend/entities/query_validator/executor.py.
- [ ] T018 [US4] Delete src/backend/entities/workflow/workflow.py.
- [ ] T019 [US4] Remove message wrapper types: `SQLDraftMessage`, `ExtractionRequestMessage`, `QueryBuilderRequestMessage`, `ClarificationMessage` from src/backend/models/generation.py and src/backend/models/extraction.py. Update src/backend/models/__init__.py re-exports.
- [ ] T020 [US4] Run import audit: `grep -r "from agent_framework import.*Executor\|WorkflowBuilder\|WorkflowContext\|handler\|response_handler" src/backend/` must return zero. Fix any remaining references.

**Checkpoint**: All MAF orchestration code is removed. Only ChatAgent/AzureAIClient/AgentThread/@tool remain.

---

## Phase 7: Update All Tests (US5)

**Goal**: Full test suite passes with the new architecture.

### Implementation

- [ ] T021 [US5] Update imports in all existing test files under tests/unit/ and tests/integration/ to use new function paths (validator.py, extractor.py, builder.py, pipeline.py). Remove any WorkflowContext or Executor mocks.
- [ ] T022 [US5] Update tests/integration/test_workflow_integration.py to test `process_query()` directly with `PipelineClients`. Remove Workflow setup.
- [ ] T023 [US5] Verify all tests pass: `uv run poe test`. Fix any failures.

**Checkpoint**: `uv run poe test` exits with code 0. Zero test files reference Executor/Workflow/WorkflowContext.

---

## Phase 8: Polish & Quality Gates

**Purpose**: Final validation across all user stories

- [ ] T024 Run `uv run poe check` — all quality gates pass (lint, typecheck, test)
- [ ] T025 Run `uv run poe metrics` — verify reduced complexity scores
- [ ] T026 Compare SSE stream output for template-based and dynamic queries against baseline (T001). Verify structure-identical.
- [ ] T027 Record final line counts of pipeline files. Calculate reduction vs baseline. Verify ≥ 40%.
- [ ] T028 Run import audit: confirm zero Executor/Workflow/WorkflowContext/handler/response_handler imports in src/backend/

---

## Dependencies & Execution Order

### Phase Dependencies

- **Baseline (Phase 1)**: No dependencies — start immediately
- **Validators (Phase 2)**: No dependencies — can start in parallel with Phase 1
- **LLM Functions (Phase 3)**: No dependencies on Phase 2 — can run in parallel
- **Pipeline (Phase 4)**: Depends on Phase 2 + Phase 3 (needs all extracted functions)
- **SSE Rewire (Phase 5)**: Depends on Phase 4 (needs `process_query()`)
- **Dead Code (Phase 6)**: Depends on Phase 5 (old code removed only after new code is wired in)
- **Tests (Phase 7)**: Starts during Phase 2 (tests alongside extraction) but finishes after Phase 6
- **Polish (Phase 8)**: Depends on Phase 6 + Phase 7

### User Story Independence

```
Phase 2 (Validators) ────┐
                          ├──► Phase 4 (Pipeline) ──► Phase 5 (SSE) ──► Phase 6 (Cleanup) ──► Phase 8 (Polish)
Phase 3 (LLM Functions) ─┘                                                    ▲
                                                                               │
                                                              Phase 7 (Tests) ─┘
```

- **Phase 2 + Phase 3**: Both can start immediately, independent (different files)
- **Phase 4**: Merges both extraction streams into `process_query()`
- **Phase 5**: Integrates pipeline into the API layer
- **Phase 6 + Phase 7**: Sequential — remove old, then verify tests
- **Phase 8**: Final validation gate

### Within Each Phase

- Extract function(s) before writing tests for them
- New module before old module deletion
- Backend before frontend (though no frontend changes expected)
- Run `uv run poe check` at each checkpoint

### Parallel Opportunities

Within Phase 2: T002 (param validator) + T003 (query validator) in parallel
Within Phase 2: T004 (param tests) + T005 (query tests) in parallel
Within Phase 3: T008 (extractor tests) + T009 (builder tests) in parallel after T006 + T007
Phase 2 and Phase 3 can run as two parallel streams

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

7. Complete Phase 6: Remove all dead code
8. Complete Phase 7: Final test updates
9. Complete Phase 8: Quality gates and measurements

### Task Count Summary

| Phase | Story | Tasks | Parallel |
|-------|-------|-------|----------|
| Baseline | — | 1 | — |
| Validators | US1 | 4 | 4 |
| LLM Functions | US1 | 4 | 2 |
| Pipeline | US1+US2 | 4 | 0 |
| SSE Rewire | US2+US3 | 3 | 0 |
| Dead Code | US4 | 4 | 0 |
| Tests | US5 | 3 | 0 |
| Polish | — | 5 | 3 |
| **Total** | | **28** | **9** |

---

## Notes

- `entities/nl2sql_controller/executor.py` (1,296 lines) is the largest file and most complex extraction — tackle in Phase 4 after all sub-functions exist
- `entities/parameter_validator/executor.py` and `entities/query_validator/executor.py` are 100% deterministic — safest to extract first
- ChatAgent instances continue to be created in `agent.py` files — no change to Foundry agent registration
- The `@tool` decorator on shared tools (`template_search`, `table_search`, `execute_sql`) is unchanged — these are independent of Executor/Workflow
- Step event emission via `asyncio.Queue` is unchanged in mechanism — only the caller changes
- Clarification flow is the riskiest change (currently uses workflow pause/resume via `RequestInfoEvent`) — Phase 5 T015 needs thorough testing
- Commit after each task or logical group; run `uv run poe check` at each checkpoint
