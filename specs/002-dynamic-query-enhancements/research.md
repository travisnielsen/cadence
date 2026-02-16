# Research: Dynamic Query Enhancements

## R1: Column Selection via Prompt Engineering

**Decision**: Enhance the QueryBuilder prompt with explicit column selectivity instructions and a `MAX_DISPLAY_COLUMNS` limit.

**Rationale**: The current prompt says "avoid `SELECT *`" but provides no guidance on column count, relevance ranking, or when to relax the limit. Adding structured instructions (prefer identity columns, metrics, WHERE/ORDER BY columns; cap at 8 unless user says "all") is the simplest and most leveraged fix — it reduces data at the SQL level.

**Alternatives considered**:

- **Post-processing only** (no prompt change): Rejected because it doesn't reduce SQL data transfer or execution time, and the LLM still generates wide queries that must be trimmed.
- **SQL rewriting**: Rejected due to fragility of parsing LLM-generated SQL to remove columns.

## R2: Empty Column Removal — Implementation Location

**Decision**: Pure function `refine_columns()` in `entities/shared/column_filter.py`, called from `NL2SQLController.handle_sql_draft()` after SQL execution, before building `NL2SQLResponse`.

**Rationale**: Following the `substitute_parameters()` pattern from spec-001 — a dependency-free pure function for testability. The controller is the right call site because it has access to both query results and `query_source` (to skip template results).

**Alternatives considered**:

- **In the orchestrator**: Rejected because the orchestrator receives the final `NL2SQLResponse` and shouldn't modify result data.
- **In the SQL tool**: Rejected because `execute_sql` is a generic tool; column filtering is a display concern.

## R3: Column Relevance Ranking for Capping

**Decision**: Rank columns by: (1) mentioned in user question (string match), (2) in GROUP BY / ORDER BY / aggregate, (3) PK or name-like columns, (4) positional order in SELECT.

**Rationale**: The first two tiers are intent-driven (what the user asked about), the third is identity-driven (context for understanding rows), the fourth is a stable tiebreaker. This ranking requires the user query string and the SQL string — both available on `SQLDraft` at the point of execution.

**Alternatives considered**:

- **LLM-based ranking**: Rejected as an unnecessary additional LLM call that adds latency and cost.
- **Column data entropy**: Rejected as over-engineered for the UX benefit.

## R4: Confidence Gate — Workflow Integration

**Decision**: Reuse the existing `ctx.request_info()` → `response_handler` pattern from the clarification flow. The NL2SQLController intercepts validated dynamic queries with `confidence < 0.7`, builds a confirmation message with `reasoning` + Accept/Revise options, and sends it as a `needs_confirmation` response. The orchestrator renders it like a clarification prompt.

**Rationale**: The MAF `request_info()` mechanism already pauses the workflow and waits for user input. The orchestrator already handles `needs_clarification` responses. Reusing this pattern avoids new infrastructure. The `CLARIFICATION_STATE_KEY` shared state already stores `dynamic_query=True` context needed for resumption.

**Alternatives considered**:

- **New `needs_confirmation` response type**: Rejected because the existing clarification pattern is sufficient. We can distinguish via a `confirmation_type` field or simply by checking `query_summary` presence.
- **Frontend-only gate (confirm before displaying results)**: Rejected because this would still execute the query, defeating the purpose of catching misinterpretations before hitting the database.

## R5: LLM Self-Assessed Confidence

**Decision**: Add a `confidence` field (0.0–1.0) to the QueryBuilder's JSON output schema. The prompt provides calibration guidance: ≥ 0.8 for clear single-table queries with explicit columns; 0.5–0.8 for multi-table joins or inferred filters; < 0.5 for vague intent or when no good column match exists.

**Rationale**: Self-assessed confidence from instruction-tuned LLMs is directionally useful — they can signal uncertainty about ambiguous questions. It's not perfectly calibrated, but it doesn't need to be; the threshold (0.7) is a UX decision that can be tuned independently.

**Alternatives considered**:

- **Table search score as proxy**: Rejected because table search scores reflect how well tables match the question, not how well the generated SQL matches the intent.
- **Deterministic confidence based on SQL complexity**: Rejected as too simplistic (a complex query can still be high-confidence if the question is clear).

## R6: Rich Column Metadata — Hydration Fix

**Decision**: Modify `_hydrate_table_metadata()` in `table_search.py` to extract all `TableColumn` fields: `data_type`, `is_nullable`, `is_primary_key`, `is_foreign_key`, `foreign_key_table`, `foreign_key_column`. No search index changes needed — the data is already indexed.

**Rationale**: The search index already contains all column-level metadata (confirmed in the table JSON files). The hydration function simply drops it by only reading `name` and `description`. This is a one-line-per-field fix. The `_build_generation_prompt()` function then passes the full metadata to the LLM.

**Alternatives considered**:

- **Separate metadata lookup (by-pass search)**: Rejected because the data is already in the search results.

## R7: Error Recovery — Suggestion Generation

**Decision**: Classify validation failures into categories (disallowed_tables, syntax, generic) and generate category-specific error messages + example questions using the existing `SchemaSuggestion` model. Populate `NL2SQLResponse.suggestions` on error paths (currently skipped by `enrich_response()`).

**Rationale**: The orchestrator already renders `SchemaSuggestion` as clickable pills. The `SCHEMA_SUGGESTIONS` lookup table already has per-area examples. For error recovery, we select examples from the schema area(s) matched in the original table search. The `enrich_response()` guard that skips suggestions on error paths needs to be relaxed.

**Alternatives considered**:

- **New `error_suggestions` field**: Rejected because `suggestions` already exists and the frontend renders it. Using the same field means zero frontend changes for error pills.
- **LLM-generated recovery suggestions**: Rejected as an unnecessary LLM call for a rare error path.

## R8: Frontend Hidden Columns Toggle

**Decision**: Add a `hidden_columns` field to the `NL2SQLResult` TypeScript interface. The `DataTable` component receives all column data but only renders `columns` initially. A "Show N more columns" button toggles visibility using local React state.

**Rationale**: The data is already in `sql_response` (all columns present in every row dict). The frontend just needs to control which columns are rendered in `<TableHeader>` and `<TableCell>`. No new API call needed — purely client-side state toggle.

**Alternatives considered**:

- **Lazy column loading**: Rejected because re-querying for hidden columns would be slower and more complex.
- **Separate "full table" modal**: Rejected as over-engineered for this use case.
