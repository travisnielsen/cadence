# Tasks: NL2SQL Confidence Scoring, Dynamic Allowed Values, Schema-Area Context, and Clarification Flows

**Input**: Design documents from `specs/nl2sql-confidence-and-clarification/`
**Prerequisites**: plan.md (required), spec.md (required for user stories)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Include exact file paths in descriptions

---

## Phase 1: Model & Schema Changes (Shared Infrastructure)

**Purpose**: Update Pydantic models and data schemas that all user stories depend on

- [x] T001 [US2] Add `ParameterConfidence` model to `src/backend/models/extraction.py` — fields: `name: str`, `value: Any`, `confidence: float`, `resolution_method: str` (enum: "exact_match", "fuzzy_match", "llm_validated", "llm_unvalidated", "default_value", "default_policy", "llm_failed_validation")
- [x] T002 [P] [US1] Add `best_guess: str | None`, `guess_confidence: float`, and `alternatives: list[str] | None` fields to `MissingParameter` in `src/backend/models/extraction.py`
- [x] T003 [P] [US2] Add `parameter_confidences: dict[str, float]` and `needs_confirmation: bool` fields to `SQLDraft` in `src/backend/models/generation.py`
- [x] T004 [P] [US4] Add `allowed_values_source: str | None` and `table: str | None` fields to `ParameterDefinition` in `src/backend/models/schema.py` — `allowed_values_source` accepts `"database"` or `None` (no `"static"` value — structural enums are identified by `allowed_values_source is None` + `validation.allowed_values` being set); `table` holds the fully-qualified table name (e.g., `"Sales.CustomerCategories"`) used with `column` to resolve database-sourced allowed values
- [x] T005 [P] [US3] Add `current_schema_area: str | None` and `schema_exploration_depth: int` fields to `ConversationContext` in `src/backend/entities/orchestrator/orchestrator.py`
- [x] T006 [P] [US2] Update `confidence_weight` from 0.4 to 1.0 for all parameters in all query templates (`infra/data/query_templates/*.json`). Date/time parameters (`from_date`, `days`) should use 0.7 instead to force confirmation on LLM-inferred dates (applies to `query_tempate_1.json` only). Also update the `ParameterDefinition.confidence_weight` field default from `0.0` to `1.0` in `src/backend/models/schema.py` so that templates loaded without an explicit weight use the correct pass-through default.

**Checkpoint**: All model changes in place. No behavior changes yet — all new fields have defaults.

---

## Phase 2: User Story 2 — Deterministic Confidence Scoring (Priority: P1) 🎯 Enabler

**Goal**: Compute per-parameter confidence scores based on resolution method. This is the enabler for US1 (clarification flows).

**Independent Test**: Run a query with mixed parameter resolution methods. Verify confidence scores are correct per the score table in plan.md.

### Tests for User Story 2

- [x] T007 [P] [US2] Create `tests/unit/test_confidence_scoring.py` with tests:
  - Exact match → confidence 1.0
  - Fuzzy match → confidence 0.85
  - Default value → confidence 0.7
  - LLM extraction with passing validation → confidence 0.75
  - LLM extraction with no validation rule → confidence 0.65
  - LLM extraction with failing validation → confidence 0.3
  - Effective confidence calculation uses `confidence_weight`
  - Min effective confidence across all params determines routing tier

### Implementation for User Story 2

- [x] T008 [US2] Add `_compute_confidence()` function to `src/backend/entities/parameter_extractor/executor.py` — accepts `resolution_method: str` and `confidence_weight: float`, returns `float`. Implements the score table from plan.md.
- [x] T009 [US2] Modify `_pre_extract_parameters()` in `src/backend/entities/parameter_extractor/executor.py` to record `resolution_method` for each parameter it resolves (exact_match, fuzzy_match, default_value). Return a `dict[str, str]` of `{param_name: resolution_method}` alongside the extracted values.
- [x] T010 [US2] Modify the LLM extraction path in `ParameterExtractorExecutor.handle_extraction_request()` to assign resolution methods (`llm_validated`, `llm_unvalidated`, `llm_failed_validation`) based on whether the parameter has validation rules and passes them.
- [x] T011 [US2] After extraction completes, compute `parameter_confidences` dict and set it on the `SQLDraft` before sending to NL2SQL controller. Use `_compute_confidence()` for each parameter.
- [x] T012 [US2] Modify `NL2SQLController.handle_sql_draft()` in `src/backend/entities/nl2sql_controller/executor.py` to check `min(draft.parameter_confidences.values())` and set routing:
  - ≥ 0.85 → proceed to execution (existing path)
  - 0.6–0.85 → set `draft.needs_confirmation = True`, proceed to execution
  - < 0.6 → trigger clarification flow (existing path but with enriched data)

**Checkpoint**: Confidence scores computed and routed. No visible UX change yet — the confirm/ask paths just set flags.

---

## Phase 3: User Story 1 — Hypothesis-First Clarification (Priority: P1) 🎯 MVP

**Goal**: Replace open-ended clarification prompts with hypothesis-first format that presents a best guess and alternatives.

**Independent Test**: Send "show me top products" and verify the clarification says "It looks like you want top products by quantity sold. Is that right, or did you mean by revenue?" instead of "What order do you want?"

### Tests for User Story 1

- [x] T013 [P] [US1] Create `tests/unit/test_clarification_format.py` with tests:
  - Clarification with best_guess and alternatives → hypothesis-first format
  - Clarification with allowed_values → alternatives pulled from allowed_values list
  - Clarification without best_guess → graceful fallback to current prompt style
  - Confirmation tier (0.6–0.85) → response includes "I assumed X — is that right?"
  - Single question per turn enforced (max 1 clarification question per response)
  - Previously extracted parameters preserved across clarification turns (FR-014)

### Implementation for User Story 1

- [x] T014 [US1] Update parameter extractor prompt in `src/backend/entities/parameter_extractor/prompt.md` to instruct the LLM: when returning `needs_clarification`, also include `best_guess` and `alternatives` for each missing parameter
- [x] T015 [US1] Modify the clarification JSON parsing in `ParameterExtractorExecutor` (`src/backend/entities/parameter_extractor/executor.py`) to extract `best_guess`, `guess_confidence`, and `alternatives` from the LLM response and populate the enriched `MissingParameter` fields
- [x] T016 [US1] Modify the deterministic clarification path in `_pre_extract_parameters()` — when a required `ask_if_missing` parameter can't be resolved, populate `best_guess` from the closest fuzzy match (if any) and `alternatives` from remaining `allowed_values`
- [x] T017 [US1] Update `NL2SQLController._build_clarification_prompt()` in `src/backend/entities/nl2sql_controller/executor.py` to format hypothesis-first prompts using `best_guess` and `alternatives` from `MissingParameter`
- [x] T018 [US1] Add confirmation text rendering in `NL2SQLController` — when `draft.needs_confirmation == True`, prepend a confirmation note to the response: "I assumed {param}={value} for these results. Want to adjust?"
- [x] T019 [US1] Add single-question enforcement in `NL2SQLController` — when multiple `MissingParameter` entries exist, clarify only the lowest-confidence one per turn. Store remaining unresolved parameters on the `SQLDraft` for subsequent turns.
- [x] T020 [US1] Ensure `on_clarification_response()` in the NL2SQL controller preserves already-extracted parameters from the `SQLDraft.extracted_parameters` when re-routing to the parameter extractor (don't re-extract confirmed params)

**Checkpoint**: Hypothesis-first clarification working end-to-end. Confirmation tier shows inline notes.

---

## Phase 4: User Story 3 — Schema-Area Contextual Suggestions (Priority: P2)

**Goal**: Track which schema area the user is exploring and suggest relevant follow-up questions, rendered as clickable pills in the tool result UI.

**Frontend Approach**: Extend existing `makeAssistantToolUI` (`NL2SQLToolUI`) — same pattern as `ClarificationOptions`. NOT `SuggestionPrimitive` (designed for welcome screens, not post-response follow-ups).

**Independent Test**: Ask "show me top customers" → verify response includes Sales-domain suggestion pills like "Explore order trends" or "Drill into invoice details." Clicking a pill auto-sends the query.

### Tests for User Story 3

- [x] T021 [P] [US3] Create `tests/unit/test_schema_area_context.py` with tests:
  - `Sales.Orders` → schema area "sales"
  - `Warehouse.StockItems` → schema area "warehouse"
  - Mixed tables (Sales + Application) → primary table determines area
  - Schema area suggestions match the SCHEMA_SUGGESTIONS dict
  - `schema_exploration_depth` increments for consecutive same-area queries
  - Cross-area suggestion triggers after 3+ consecutive queries in same area
  - `SchemaSuggestion` model serializes to JSON with `title` and `prompt` fields

### Backend Implementation for User Story 3

- [x] T022 [US3] Add `SchemaSuggestion` model to `src/backend/models/execution.py` with `title: str` and `prompt: str` fields. Add `suggestions: list[SchemaSuggestion] = []` field to `NL2SQLResponse`.
- [x] T023 [US3] Add `SCHEMA_SUGGESTIONS` dict (keyed by area, values are `list[SchemaSuggestion]` with both `title` and `prompt`) and `_detect_schema_area(tables: list[str]) -> str | None` helper function to `src/backend/entities/orchestrator/orchestrator.py`
- [x] T024 [US3] Modify `ConversationOrchestrator.update_context()` to call `_detect_schema_area()` using tables from the query result. For template-based queries where `response.tables_used` is empty, parse table names from `response.sql_query` (e.g., regex `FROM\s+([\w.]+)` extracts `Schema.Table` from the completed SQL). For dynamic queries, use `response.tables_used` directly. Update `current_schema_area` and increment/reset `schema_exploration_depth`.
- [x] T025 [US3] Add `_build_suggestions(schema_area: str | None, depth: int) -> list[SchemaSuggestion]` method to orchestrator that selects 2–3 relevant suggestions based on area and depth. At depth ≥ 3, include one cross-area suggestion. If `schema_area is None`, return an empty list (no suggestions for undetectable schema areas).
- [x] T026 [US3] Add suggestions to the `NL2SQLResponse` in the orchestrator's `_handle_nl2sql_result()` method — **after** the NL2SQL workflow returns but **before** passing to the frontend SSE stream. The orchestrator (not a workflow executor) owns suggestion logic because it has access to `ConversationContext` (schema area, depth). Call `_build_suggestions()` and set `response.suggestions`.
- [x] T027 [US3] Update orchestrator prompt (`src/backend/entities/orchestrator/orchestrator_prompt.md`) to instruct the LLM to incorporate schema-area context when generating conversational responses and empty-result recovery suggestions. When `NL2SQLResponse.sql_response` is empty, `_build_suggestions` should include recovery suggestions ("Try a broader date range", "Check related tables") in addition to standard schema-area suggestions.

### Frontend Implementation for User Story 3

- [x] T028 [US3] Add `SuggestionPills` React component to `src/frontend/components/assistant-ui/nl2sql-tool-ui.tsx` — renders suggestion pills with green/neutral styling (differentiated from clarification's blue). Uses `threadRuntime.composer.setText(prompt)` + `.send()` on click.
- [x] T029 [US3] Add `suggestions` field to the `NL2SQLResult` TypeScript interface in `nl2sql-tool-ui.tsx`. Render `<SuggestionPills>` after the Observations section in the success state of `NL2SQLToolUI`, only when `result.suggestions?.length > 0`.

**Checkpoint**: Schema-area suggestions appearing as clickable pills in query results. Context tracks across turns. Clicking a pill sends the follow-up query.

---

## Phase 5: User Story 4 — Dynamic Allowed Values Cache (Priority: P3)

**Goal**: Load and cache allowed values from the database at runtime for parameters that reference dynamic data.

**Independent Test**: Configure a parameter with `allowed_values_source: "database"`. Verify it queries `SELECT DISTINCT`, caches the result, hydrates `param.validation.allowed_values`, and that all downstream consumers (fuzzy match, LLM prompt, validator) use the hydrated values.

### Tests for User Story 4

- [ ] T030 [P] [US4] Create `tests/unit/test_allowed_values_provider.py` with tests:
  - Cache miss → triggers async DB query, returns values
  - Cache hit within TTL → returns cached values without DB query
  - Cache expired → returns stale values, triggers background refresh
  - Column with > 500 values → caps at 500, sets `is_partial` flag
  - DB error during load → returns empty list, logs warning, retries on next call
  - `get_allowed_values()` for static source → returns None (not handled by provider)
  - TTL is configurable via environment variable
  - Hydrated values flow to `_fuzzy_match_allowed_value()` (existing fuzzy match logic works unchanged)
  - Hydrated values flow to `_build_extraction_prompt()` (LLM sees values in prompt)
  - Hydrated values flow to `ParameterValidator._validate_string()` (strict match works unchanged)
  - Partial cache (`is_partial=True`) → validator skips strict `allowed_values` check for that parameter
  - Structural enum params (`allowed_values_source: null`, `validation.allowed_values: ["ASC","DESC"]`) → not affected by hydration, validator checks static list as before
  - Database-sourced param with DB unreachable → `validation.allowed_values` stays `null`, falls back to LLM-only extraction

### Implementation for User Story 4

- [ ] T031 [US4] Create `src/backend/entities/shared/allowed_values_provider.py` with `AllowedValuesProvider` class:
  - Async singleton with `get_allowed_values(table: str, column: str) -> list[str] | None`
  - Internal cache: `dict[tuple[str, str], CacheEntry]` where `CacheEntry` has `values`, `loaded_at`, `is_partial`
  - Configurable `ttl_seconds` (default 600) and `max_values` (default 500)
  - Background refresh via `asyncio.create_task()` on TTL expiry
  - Reuse `AzureSqlClient` from `src/backend/entities/shared/clients/sql_client.py` — do NOT duplicate SQL connection logic
- [ ] T032 [US4] Add provider initialization in `src/backend/entities/workflow/workflow.py` — instantiate `AllowedValuesProvider` once alongside the existing client singletons, pass to `ParameterExtractorExecutor` constructor
- [ ] T033 [US4] Modify `ParameterExtractorExecutor.__init__()` to accept optional `allowed_values_provider` parameter. Store as instance attribute.
- [ ] T034 [US4] Add `_hydrate_database_allowed_values()` method to `ParameterExtractorExecutor` in `src/backend/entities/parameter_extractor/executor.py` — called at the **start** of `handle_extraction_request()`, before `_pre_extract_parameters()`. For each parameter where `allowed_values_source == "database"` and `table` + `column` are set:
  - Call `provider.get_allowed_values(param.table, param.column)` to get cached values
  - Set `param.validation.allowed_values = db_values` (create `ParameterValidation(type="string", allowed_values=db_values)` if `param.validation` is None)
  - If provider returns `is_partial=True`, set a flag on the param so the validator skips strict `allowed_values` matching
  - Skip hydration for params where `allowed_values_source` is `null` — their `validation.allowed_values` (if any) are static structural enums and must not be overwritten
  - This "hydrate once" approach means **no changes** are needed in `_fuzzy_match_allowed_value()`, `_build_extraction_prompt()`, or `ParameterValidator._validate_string()` — they all read `param.validation.allowed_values` as normal
- [ ] T035 [US4] Update `query_template_8.json` to use `allowed_values_source: "database"` for the `category_name` parameter: add `"table": "Sales.CustomerCategories"`, set `"allowed_values_source": "database"`, and **remove** `allowed_values` from the `validation` block (keep `"type": "string"` only). Update the corresponding AI Search index if the schema needs new fields for `allowed_values_source` and `table`.

**Checkpoint**: Dynamic allowed values working for at least one parameter. Cache populated and refreshed.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, final integration, and cleanup

- [ ] T036 [P] Update `src/backend/entities/nl2sql_controller/prompt.md` schema reference to include all 31 tables (currently lists a subset)
- [ ] T037 [P] Update NL2SQL controller prompt to describe the confidence tiers and when to show confirmation text vs. trigger clarification
- [ ] T038 [P] Add integration-level tests in `tests/integration/` that exercise the full workflow: query → confidence scoring → clarification → execution
- [ ] T039 Run `uv run poe check` and fix any lint/type/format issues across all changed files
- [ ] T040 Update `GLOSSARY.md` with new terms: effective confidence, hypothesis-first clarification, schema area, allowed values cache

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Models)**: No dependencies — start immediately. BLOCKS all subsequent phases.
- **Phase 2 (Confidence Scoring)**: Depends on Phase 1 (T001, T003)
- **Phase 3 (Clarification)**: Depends on Phase 1 (T002) AND Phase 2 (routing logic)
- **Phase 4 (Schema Area)**: Depends on Phase 1 (T005) only — can run in parallel with Phase 2/3
- **Phase 5 (Cache)**: Depends on Phase 1 (T004) only — can run in parallel with Phase 2/3/4
- **Phase 6 (Polish)**: Depends on all previous phases

### Parallel Opportunities

```
Phase 1: T001, T002, T003, T004, T005, T006 — all parallel (different files)
     │
     ├── Phase 2: T007→T008→T009→T010→T011→T012 (sequential within)
     │        │
     │        └── Phase 3: T013→T014→T015→T016→T017→T018→T019→T020 (sequential, needs Phase 2)
     │
     ├── Phase 4: T021→T022→T023→T024→T025→T026→T027 (backend)
     │                                          └→T028→T029 (frontend, after T022 model)
     │
     └── Phase 5: T030→T031→T032→T033→T034→T035 (independent of Phase 2/3/4)
              │
Phase 6: T036, T037, T038, T039, T040 — after all phases
```

### Implementation Strategy

**Recommended: Sequential by priority**

1. Phase 1 (all model changes) → validate with `uv run poe check`
2. Phase 2 (confidence scoring) → test independently
3. Phase 3 (clarification UX) → test end-to-end with Phase 2
4. Phase 4 (schema suggestions) → can be done anytime after Phase 1
5. Phase 5 (dynamic cache) → can be done anytime after Phase 1
6. Phase 6 (polish) → after all features stable

Each phase produces independently valuable functionality:

- After Phase 2: System has confidence scores (internal improvement, no UX change)
- After Phase 3: Users see better clarification UX (biggest user-facing impact)
- After Phase 4: Users get contextual suggestions (discoverability improvement)
- After Phase 5: System handles dynamic data (production readiness)

---

## Notes

- All new code must be async-first per constitution
- All models must use Pydantic with Field descriptions
- Run `uv run poe check` after each phase
- Confidence thresholds should be configurable via environment variables for tuning
- `confidence_weight` defaults to 1.0 (pass-through). Set < 1.0 on critical parameters to force confirm/ask tiers. Floor of 0.3 prevents zeroing.
