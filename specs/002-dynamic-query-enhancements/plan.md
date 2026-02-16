# Implementation Plan: Dynamic Query Enhancements

**Branch**: `002-dynamic-query-enhancements` | **Date**: 2026-02-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-dynamic-query-enhancements/spec.md`

## Summary

Improve the dynamic SQL generation path (QueryBuilder) with six enhancements across three priority tiers:

- **P1 — Column Selectivity & Empty Removal** (US1, US2): Enhance the QueryBuilder prompt with explicit column-count guidance and add a post-execution pure function that strips fully-empty columns.
- **P2 — Rich Metadata, Column Capping UI, Confidence Gate** (US3, US4, US5): Hydrate all `TableColumn` fields into the generation prompt, cap visible columns with a frontend expand toggle, and gate low-confidence dynamic queries with a confirmation step before execution.
- **P3 — Actionable Error Recovery** (US6): Classify validation failures and present user-friendly guidance with clickable suggestion pills instead of raw error dumps.

All changes are scoped to the dynamic path — template-based queries pass through unmodified. See [research.md](research.md) for design decisions and [data-model.md](data-model.md) for model changes.

## Technical Context

**Language/Version**: Python 3.11+, TypeScript/Next.js
**Primary Dependencies**: FastAPI, Microsoft Agent Framework (MAF), Pydantic, React, assistant-ui, Tailwind CSS
**Storage**: Azure SQL (query execution), Azure AI Search (table/template metadata)
**Testing**: pytest with pytest-asyncio, `uv run poe check` (ruff format + ruff lint + basedpyright)
**Target Platform**: Linux server (backend), Browser (frontend)
**Project Type**: Web application (Python backend + Next.js frontend)
**Performance Goals**: No additional LLM calls for column filtering/capping; confidence gate adds zero latency for high-confidence queries
**Constraints**: `MAX_DISPLAY_COLUMNS` default 8; confidence threshold 0.7; column filter is a pure function (no I/O)
**Scale/Scope**: 6 user stories, 23 functional requirements, ~12 files modified, ~3 new files, ~15 new tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Async-First | **PASS** | Column filter is a sync pure function (no I/O). Confidence gate uses existing async `ctx.request_info()` pattern. No new blocking I/O. |
| II. Validated Data at Boundaries | **PASS** | New fields on existing Pydantic models (`SQLDraft.confidence`, `NL2SQLResponse.hidden_columns`, etc.). `ColumnRefinementResult` is a frozen dataclass for internal use only (does not cross API boundary). |
| III. Fully Typed | **PASS** | All new functions and fields fully typed with annotations. |
| IV. Single-Responsibility Executors | **PASS** | Column filter is a shared utility (like `substitution.py`). Confidence gate logic stays in NL2SQLController (which already owns routing). Error recovery stays in NL2SQLController (which already owns error building). No new executors needed. |
| V. Automated Quality Gates | **PASS** | `uv run poe check` must pass before every commit. New unit tests required for each component. |

**Post-Phase 1 Re-check**: All principles still satisfied. No new executors, no blocking I/O in new code, all API boundaries use Pydantic models.

## Project Structure

### Documentation (this feature)

```text
specs/002-dynamic-query-enhancements/
├── plan.md              # This file
├── spec.md              # Feature specification (6 user stories, 23 FRs)
├── research.md          # Phase 0: 8 research decisions (R1–R8)
├── data-model.md        # Phase 1: Model changes
├── quickstart.md        # Phase 1: Setup and file map
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (changes by area)

```text
src/backend/
├── models/
│   ├── generation.py            # MODIFY: Add confidence field to SQLDraft
│   └── execution.py             # MODIFY: Add hidden_columns, query_summary, query_confidence, error_suggestions to NL2SQLResponse
├── entities/
│   ├── shared/
│   │   ├── column_filter.py     # NEW: refine_columns() pure function + ColumnRefinementResult
│   │   └── tools/
│   │       └── table_search.py  # MODIFY: Hydrate all TableColumn fields in _hydrate_table_metadata()
│   ├── query_builder/
│   │   ├── prompt.md            # MODIFY: Column selectivity instructions + confidence JSON field
│   │   └── executor.py          # MODIFY: Parse confidence, pass rich metadata in prompt
│   ├── nl2sql_controller/
│   │   └── executor.py          # MODIFY: Call refine_columns(), confidence gate, error recovery
│   └── orchestrator/
│       └── orchestrator.py      # MODIFY: Allow suggestions on error responses

src/frontend/components/
├── assistant-ui/
│   └── nl2sql-tool-ui.tsx       # MODIFY: Hidden columns toggle, confirmation UI, error suggestion pills
└── tool-ui/data-table/
    └── data-table.tsx           # MODIFY: Column visibility state via prop

tests/unit/
├── test_column_filter.py        # NEW: Column stripping, capping, ranking, edge cases
├── test_confidence_gate.py      # NEW: Confidence routing, confirmation flow
└── test_error_recovery.py       # NEW: Error classification, suggestion generation
```

**Structure Decision**: Web application structure (existing). Backend changes concentrated in `entities/shared/` (new column filter utility), `entities/query_builder/` (prompt + executor), and `entities/nl2sql_controller/` (routing + error handling). Frontend changes in two components. No new directories created beyond the single `column_filter.py` module.

## Implementation Phases

### Phase 1: P1 — Column Selectivity & Empty Removal (US1, US2)

**Goal**: Dynamic queries produce fewer columns at generation time; fully-empty columns are stripped post-execution.

#### 1.1 Models — Add new fields

**File**: `src/backend/models/generation.py`

- Add `confidence: float = Field(default=0.0, ge=0.0, le=1.0)` to `SQLDraft`

**File**: `src/backend/models/execution.py`

- Add to `NL2SQLResponse`:
  - `hidden_columns: list[str] = Field(default_factory=list)`
  - `query_summary: str = Field(default="")`
  - `query_confidence: float = Field(default=0.0, ge=0.0, le=1.0)`
  - `error_suggestions: list[SchemaSuggestion] = Field(default_factory=list)`

#### 1.2 Column filter utility (NEW)

**File**: `src/backend/entities/shared/column_filter.py`

- `ColumnRefinementResult` frozen dataclass: `columns`, `hidden_columns`, `rows`
- `refine_columns(columns, rows, user_query, sql, max_cols=8) -> ColumnRefinementResult`
  - Step 1: Find empty columns (all values `None` or `""`)
  - Step 2: Strip empty columns from `columns` list (preserve order)
  - Step 3: If remaining count > `max_cols`, rank by relevance and cap
  - Step 4: Return result with `hidden_columns` set
- `_rank_columns(columns, user_query, sql) -> list[str]` — sort by relevance tiers:
  (1) mentioned in user question, (2) in GROUP BY / ORDER BY / aggregate, (3) PK or name-like, (4) positional
- Edge cases: zero rows → return as-is; single column → never strip; all empty → return as-is

#### 1.3 QueryBuilder prompt — Column selectivity

**File**: `src/backend/entities/query_builder/prompt.md`

- Add "Column Selection Rules" section:
  - Select at most 8 columns relevant to the question
  - Prefer: identity/name columns, the metric asked about, columns in WHERE/ORDER BY
  - Avoid: audit timestamps, internal IDs, system fields unless explicitly requested
  - If user says "all columns" or "full details", include all relevant columns
- Add `confidence` field (0.0–1.0) to JSON output schema
- Add confidence calibration guidance (≥ 0.8 clear intent, 0.5–0.8 inferred, < 0.5 vague)

#### 1.4 QueryBuilder executor — Parse confidence + rich metadata

**File**: `src/backend/entities/query_builder/executor.py`

- In `_build_generation_prompt()`: Include `data_type`, `is_primary_key`, `is_foreign_key`, `foreign_key_table`, `foreign_key_column`, `is_nullable` per column (US4 metadata, one-touch change since we're already modifying this function)
- In `handle_query_build_request()`: Parse `confidence` from LLM JSON response, set on `SQLDraft`
- Default confidence to 0.5 if LLM omits the field

#### 1.5 NL2SQLController — Call column filter

**File**: `src/backend/entities/nl2sql_controller/executor.py`

- After successful dynamic SQL execution (around line 813), call `refine_columns()` on the result
- Populate `NL2SQLResponse.columns` and `hidden_columns` from `ColumnRefinementResult`
- Skip filter for template results (check `query_source`)
- Pass `sql_draft.confidence` → `NL2SQLResponse.query_confidence`

#### 1.6 Tests

**File**: `tests/unit/test_column_filter.py` (NEW)

- Empty column detection (all NULL, all empty string, mixed NULL/empty)
- Partial data retention (one non-null value keeps column)
- Column capping with ranking
- Edge cases: zero rows, single column, all columns empty, column order preservation
- `max_cols` parameter customization

---

### Phase 2: P2 — Rich Metadata, Column UI, Confidence Gate (US3, US4, US5)

**Goal**: Full column metadata in prompts, frontend expand toggle for hidden columns, confidence-gated confirmation for uncertain queries.

#### 2.1 Table search hydration

**File**: `src/backend/entities/shared/tools/table_search.py`

- Modify `_hydrate_table_metadata()` to extract all `TableColumn` fields:
  `data_type`, `is_nullable`, `is_primary_key`, `is_foreign_key`, `foreign_key_table`, `foreign_key_column`
- No search index changes needed — data is already indexed

#### 2.2 NL2SQLController — Confidence gate

**File**: `src/backend/entities/nl2sql_controller/executor.py`

- After query validation passes for dynamic queries (before execution):
  - If `sql_draft.confidence < DYNAMIC_CONFIDENCE_THRESHOLD` and not a refinement turn:
    - Build `query_summary` from `sql_draft.reasoning` (fallback: parse tables + key columns from SQL)
    - Return `NL2SQLResponse` with `needs_clarification=True`, `query_summary`, `query_confidence`, `sql_query`, no `sql_response`
    - Store pending query in `CLARIFICATION_STATE_KEY` for resumption
  - If confidence >= threshold or refinement turn: execute immediately
- On user acceptance: execute the stored SQL and return results
- On user revision: re-route to QueryBuilder with revised intent

#### 2.3 Frontend — Hidden columns toggle

**File**: `src/frontend/components/assistant-ui/nl2sql-tool-ui.tsx`

- Add `hidden_columns?: string[]`, `query_summary?: string`, `query_confidence?: number`, `error_suggestions?: SchemaSuggestion[]` to `NL2SQLResult` interface
- When `hidden_columns` is non-empty, render "Show N more columns" button below the data table
- Clicking toggles local React state to include hidden columns in the DataTable render

**File**: `src/frontend/components/tool-ui/data-table/data-table.tsx`

- Accept optional `visibleColumns` prop (defaults to all columns)
- Filter rendered `<TableHeader>` and `<TableCell>` to `visibleColumns`

#### 2.4 Frontend — Confirmation UI

**File**: `src/frontend/components/assistant-ui/nl2sql-tool-ui.tsx`

- When `query_summary` is present and `needs_clarification` is true:
  - Render confirmation card: query summary text, "Run this query" button, "Revise" button
  - Reuse existing SSE `threadRuntime.composer.setText()` + `.send()` pattern

#### 2.5 Tests

**File**: `tests/unit/test_confidence_gate.py` (NEW)

- High-confidence → immediate execution
- Low-confidence → confirmation response with `needs_clarification=True`
- Refinement turn → skip confirmation regardless of confidence
- Acceptance → execution path
- Revision → re-route to QueryBuilder

---

### Phase 3: P3 — Error Recovery (US6)

**Goal**: Failed dynamic queries provide actionable guidance instead of raw validation dumps.

#### 3.1 Error classification and recovery

**File**: `src/backend/entities/nl2sql_controller/executor.py`

- New helper: `_build_error_recovery(violations, tables_searched, schema_area) -> tuple[str, list[SchemaSuggestion]]`
  - Classify violations: `disallowed_tables`, `syntax`, `generic`
  - Build category-specific user-friendly message (no raw violation dump)
  - Select 2–3 example suggestions from `SCHEMA_SUGGESTIONS` based on matched schema area
  - Fallback to generic suggestions if no table matches
- Replace current error-building code at max-retry path
- Populate `NL2SQLResponse.error` (friendly message) and `NL2SQLResponse.error_suggestions`

#### 3.2 Orchestrator — Allow suggestions on errors

**File**: `src/backend/entities/orchestrator/orchestrator.py`

- Modify `enrich_response()` to pass through `error_suggestions` even when `response.error` is set
- Map `error_suggestions` to `suggestions` key in the tool result dict for frontend rendering

#### 3.3 Frontend — Error suggestion pills

No additional frontend changes needed — `error_suggestions` maps to the existing `suggestions` rendering in `nl2sql-tool-ui.tsx`. The clickable pill UI already exists for clarification options.

#### 3.4 Tests

**File**: `tests/unit/test_error_recovery.py` (NEW)

- Disallowed-table classification → schema area suggestions
- Syntax error classification → "try simpler question" message
- Generic failure → generic guidance
- No table matches → generic fallback
- Suggestion count (2–3 per error)

## Phase Dependencies

```
Phase 1 (P1: US1, US2)
  ├── Models (1.1) — must be first (other steps depend on new fields)
  ├── Column filter (1.2) — independent pure function
  ├── Prompt + executor (1.3, 1.4) — can parallel with 1.2
  └── Controller integration (1.5) — depends on 1.1, 1.2

Phase 2 (P2: US3, US4, US5) — depends on Phase 1 models
  ├── Table hydration (2.1) — independent
  ├── Confidence gate (2.2) — depends on 1.1 (SQLDraft.confidence)
  ├── Frontend column toggle (2.3) — depends on 1.1 (hidden_columns field)
  └── Frontend confirmation (2.4) — depends on 2.2

Phase 3 (P3: US6) — depends on Phase 1 models
  ├── Error classification (3.1) — independent
  ├── Orchestrator update (3.2) — depends on 3.1
  └── Tests (3.4) — depends on 3.1
```

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LLM ignores column selectivity prompt | Medium | Low | Post-execution column cap (US3) catches over-selection |
| LLM confidence is poorly calibrated | Medium | Low | Threshold is tunable; worst case is unnecessary confirmations (friction, not breakage) |
| Column ranking produces unexpected results | Low | Low | Ranking is a heuristic fallback; most queries won't exceed 8 columns after prompt improvement |
| Confirmation gate disrupts SSE streaming | Low | Medium | Reuses existing `request_info()` / clarification pattern already proven in production |
| Table hydration changes break existing queries | Low | Medium | Additive change only (new fields default to empty/false); existing behavior unchanged |

## Complexity Tracking

No constitution violations. No complexity justifications needed.
