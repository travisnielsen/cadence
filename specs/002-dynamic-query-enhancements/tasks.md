# Tasks: Dynamic Query Enhancements

**Input**: Design documents from `/specs/002-dynamic-query-enhancements/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: Included — spec success criteria SC-010 explicitly requires unit tests for column stripping, capping, relevance ranking, confirmation gating, error recovery, and edge cases.

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story (US1–US6) this task belongs to
- Exact file paths included in descriptions (relative to `src/backend/` for backend, `src/frontend/` for frontend)

---

## Phase 1: Setup

**Purpose**: Verify baseline before starting implementation

- [x] T001 Verify baseline passes with `uv run poe check` and `uv run poe test`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Model changes shared across ALL user stories — must complete before any story starts

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 [P] Add `confidence: float = Field(default=0.0, ge=0.0, le=1.0)` to SQLDraft in src/backend/models/generation.py
- [x] T003 [P] Add `hidden_columns`, `query_summary`, `query_confidence`, `error_suggestions` fields to NL2SQLResponse in src/backend/models/execution.py

**Checkpoint**: Model fields available — user story implementation can begin

---

## Phase 3: US1 — Selective Column Generation (P1) :dart: MVP

**Goal**: QueryBuilder LLM generates SQL with at most 8 relevant columns instead of selecting all columns. Confidence field added to JSON output for downstream use.

**Independent Test**: Ask "what are the top 10 customers by order count?" against a 15+ column table. Verify generated SQL selects only identity/name columns plus the aggregated count.

### Implementation

- [x] T004 [P] [US1] Add "Column Selection Rules" section and `confidence` field with calibration guidance to QueryBuilder JSON schema in src/backend/entities/query_builder/prompt.md
- [x] T005 [P] [US1] Parse `confidence` from LLM JSON response in `handle_query_build_request()`, default to 0.5 if omitted, and set on SQLDraft in src/backend/entities/query_builder/executor.py
- [x] T006 [US1] Pass `sql_draft.confidence` to `NL2SQLResponse.query_confidence` after dynamic SQL execution in src/backend/entities/nl2sql_controller/executor.py

**Checkpoint**: Dynamic queries are prompted to limit columns; confidence score flows through the pipeline

---

## Phase 4: US2 — Empty Column Removal (P1)

**Goal**: Post-execution filter strips columns where every row value is NULL or empty string. Includes relevance-based capping for US3.

**Independent Test**: Execute a dynamic query returning 10 rows where 2 of 8 columns are entirely NULL. Verify response excludes those 2 columns from `columns` list.

### Implementation

- [x] T007 [US2] Create `column_filter.py` with `ColumnRefinementResult` frozen dataclass, `refine_columns()` pure function (empty column detection, stripping, capping, hidden_columns), and `_rank_columns()` relevance ranking in src/backend/entities/shared/column_filter.py
- [x] T008 [US2] Call `refine_columns()` after dynamic SQL execution, populate `NL2SQLResponse.columns` and `hidden_columns` from result, skip for template queries in src/backend/entities/nl2sql_controller/executor.py

### Tests

- [x] T009 [P] [US2] Create unit tests for empty column detection (all NULL, all empty string, mixed), partial data retention, column capping with ranking, edge cases (zero rows, single column, all empty, order preservation), and `max_cols` parameter in tests/unit/test_column_filter.py

**Checkpoint**: Dynamic query results have empty columns stripped and excess columns capped with `hidden_columns` populated

---

## Phase 5: US3 — Column Capping UI (P2)

**Goal**: Frontend renders "Show N more columns" toggle when `hidden_columns` is non-empty. Expanding reveals hidden columns from existing data without a backend round-trip.

**Independent Test**: Force a dynamic query returning 12 non-empty columns. Verify frontend shows 8 columns with a "Show 4 more columns" button that reveals the rest client-side.

### Implementation

- [x] T010 [P] [US3] Add `hidden_columns`, `query_summary`, `query_confidence`, `error_suggestions` to `NL2SQLResult` interface and render "Show N more columns" toggle with local React state in src/frontend/components/assistant-ui/nl2sql-tool-ui.tsx
- [x] T011 [P] [US3] Add optional `visibleColumns` prop to DataTable, filter rendered `<TableHeader>` and `<TableCell>` to visible columns only in src/frontend/components/tool-ui/data-table/data-table.tsx

**Checkpoint**: Hidden columns expandable in the frontend without re-querying

---

## Phase 6: US4 — Rich Column Metadata in Generation Prompt (P2)

**Goal**: QueryBuilder receives `data_type`, PK/FK flags, and foreign key references per column, enabling better JOINs and type-appropriate filters.

**Independent Test**: Ask a JOIN question between `Sales.Orders` and `Sales.Customers`. Verify the generation prompt includes FK references and the generated SQL uses correct join conditions.

### Implementation

- [x] T012 [US4] Hydrate all `TableColumn` fields (`data_type`, `is_nullable`, `is_primary_key`, `is_foreign_key`, `foreign_key_table`, `foreign_key_column`) in `_hydrate_table_metadata()` in src/backend/entities/shared/tools/table_search.py
- [x] T013 [US4] Format rich column metadata (data_type, PK/FK flags, foreign key references, nullable) per column in `_build_generation_prompt()` in src/backend/entities/query_builder/executor.py

**Checkpoint**: LLM receives full column metadata for better SQL generation

---

## Phase 7: US5 — Confidence-Gated Confirmation (P2)

**Goal**: Dynamic queries with confidence < 0.7 show a natural-language summary with Accept/Revise options before executing. High-confidence queries execute immediately. Refinement turns skip the gate.

**Independent Test**: Ask "show me the important purchase data" (vague). Verify confirmation prompt appears with summary. Ask "top 10 customers by order count" (clear). Verify immediate execution.

### Implementation

- [x] T014 [US5] Implement confidence gate in NL2SQLController: threshold check before execution, build `query_summary` from reasoning (fallback to SQL-derived), return `needs_clarification=True` response, store pending query in `CLARIFICATION_STATE_KEY`, handle acceptance (execute) and revision (re-route to QueryBuilder), skip gate on refinement turns in src/backend/entities/nl2sql_controller/executor.py
- [x] T015 [US5] Add confirmation card UI: render query summary text, "Run this query" and "Revise" buttons when `query_summary` is present and `needs_clarification` is true, reuse existing SSE composer pattern in src/frontend/components/assistant-ui/nl2sql-tool-ui.tsx

### Tests

- [x] T016 [P] [US5] Create unit tests for high-confidence immediate execution, low-confidence confirmation response, refinement turn bypass, acceptance execution path, and revision re-route in tests/unit/test_confidence_gate.py

**Checkpoint**: Low-confidence dynamic queries require user confirmation; high-confidence queries flow through without friction

---

## Phase 8: US6 — Actionable Error Recovery (P3)

**Goal**: Failed dynamic queries show user-friendly error messages with 2–3 clickable example question pills instead of raw validation dumps.

**Independent Test**: Submit a question referencing a disallowed table, ensure retry also fails. Verify error message says "Try asking about Sales, Purchasing, or Warehouse data" with example question pills.

### Implementation

- [x] T017 [US6] Create `_build_error_recovery()` helper: classify violations (disallowed_tables, syntax, generic), build category-specific friendly message, select 2–3 `SchemaSuggestion` examples from matched schema area, fallback to generic if no table matches, replace current max-retry error path in src/backend/entities/nl2sql_controller/executor.py
- [x] T018 [US6] Relax `enrich_response()` guard to pass through `error_suggestions` even when `response.error` is set, map to `suggestions` key in tool result dict in src/backend/entities/orchestrator/orchestrator.py

### Tests

- [x] T019 [P] [US6] Create unit tests for disallowed-table classification, syntax error classification, generic failure fallback, no-table-matches generic guidance, and suggestion count (2–3) in tests/unit/test_error_recovery.py

**Checkpoint**: Failed dynamic queries provide actionable guidance with clickable suggestion pills

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Final validation across all user stories

- [x] T020 Run full quality checks with `uv run poe check` and `uv run poe test`
- [x] T021 [P] Verify template-based queries pass through unmodified (no regression from US1–US6 changes)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — **BLOCKS all user stories**
- **US1 (Phase 3)**: Depends on Phase 2 (needs `SQLDraft.confidence` field)
- **US2 (Phase 4)**: Depends on Phase 2 (needs `NL2SQLResponse.hidden_columns` field). Can run in parallel with US1.
- **US3 (Phase 5)**: Depends on Phase 4 (backend capping logic must exist to produce `hidden_columns`)
- **US4 (Phase 6)**: Depends on Phase 2 only — can run in parallel with US1/US2/US3
- **US5 (Phase 7)**: Depends on Phase 3 (needs `SQLDraft.confidence` populated by QueryBuilder)
- **US6 (Phase 8)**: Depends on Phase 2 only — can run in parallel with other stories
- **Polish (Phase 9)**: Depends on all desired user stories being complete

### User Story Independence

```
Phase 2 (Foundational) ─┬─► US1 (P1) ──────────────► US5 (P2) ──┐
                         ├─► US2 (P1) ──► US3 (P2)               ├─► Polish
                         ├─► US4 (P2)                             │
                         └─► US6 (P3) ───────────────────────────┘
```

- **US1 + US2**: Both P1, can run in parallel after Foundational
- **US3**: Depends on US2 (backend capping), handles only frontend
- **US4**: Independent — can start any time after Foundational
- **US5**: Depends on US1 (confidence must be parsed before gate can check it)
- **US6**: Independent — can start any time after Foundational

### Within Each User Story

- Models/utilities before controller integration
- Backend before frontend
- Implementation before tests (tests validate the implementation)
- Story complete before moving to next priority

---

## Parallel Execution Examples

### After Phase 2: Two Parallel Streams

```
Stream A (Column Quality):     US1 (T004-T006) → US2 (T007-T009) → US3 (T010-T011)
Stream B (Metadata + Errors):  US4 (T012-T013) in parallel with US6 (T017-T019)
Then: US5 (T014-T016) after US1 completes
```

### Within US2: Parallel Tasks

```
Sequential: T007 (create column_filter.py) → T008 (controller integration)
Parallel:   T009 (tests) can start after T007 completes (tests the pure function)
```

### Within US5: Parallel Tasks

```
Sequential: T014 (backend gate) → T015 (frontend confirmation)
Parallel:   T016 (tests) can start after T014 completes
```

---

## Implementation Strategy

### MVP First (US1 + US2 Only)

1. Complete Phase 1: Setup (verify baseline)
2. Complete Phase 2: Foundational (model changes)
3. Complete Phase 3: US1 — Selective Column Generation
4. Complete Phase 4: US2 — Empty Column Removal
5. **STOP and VALIDATE**: Dynamic queries produce fewer, cleaner columns
6. Deploy/demo if ready — this alone delivers significant UX improvement

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US1 + US2 → Column quality MVP (P1 complete) → Validate
3. US3 → Frontend column toggle → Validate
4. US4 → Richer prompts → Validate
5. US5 → Confidence gate → Validate
6. US6 → Error recovery → Validate
7. Each story adds value without breaking previous stories

### Task Count Summary

| Phase | Story | Tasks | Parallel |
|-------|-------|-------|----------|
| Setup | — | 1 | — |
| Foundational | — | 2 | 2 |
| US1 (P1) | Selective Columns | 3 | 2 |
| US2 (P1) | Empty Removal | 3 | 1 |
| US3 (P2) | Column Cap UI | 2 | 2 |
| US4 (P2) | Rich Metadata | 2 | 0 |
| US5 (P2) | Confidence Gate | 3 | 1 |
| US6 (P3) | Error Recovery | 3 | 1 |
| Polish | — | 2 | 1 |
| **Total** | | **21** | **10** |

---

## Notes

- All backend paths relative to `src/backend/`; frontend paths relative to `src/frontend/`
- `entities/nl2sql_controller/executor.py` is touched by US1 (T006), US2 (T008), US5 (T014), US6 (T017) — these are in sequential phases, no conflicts
- `entities/query_builder/executor.py` is touched by US1 (T005) and US4 (T013) — different functions, sequential phases
- `nl2sql-tool-ui.tsx` is touched by US3 (T010) and US5 (T015) — different UI sections, sequential phases
- Template-based queries must pass through unmodified (validated in T021)
- Commit after each task or logical group; run `uv run poe check` after each change
