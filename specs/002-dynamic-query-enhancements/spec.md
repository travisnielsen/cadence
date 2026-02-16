# Feature Specification: Dynamic Query Enhancements

**Feature Branch**: `002-dynamic-query-enhancements`
**Created**: 2026-02-15
**Status**: Draft
**Input**: User request to improve the low-confidence (dynamic SQL generation) path — column selectivity, result trimming, richer metadata, confidence-gated confirmation, and actionable error recovery.

## Context

The NL2SQL system has two query paths:

1. **Template path** (high confidence) — Developer-authored SQL templates with parameter extraction. These are curated and assumed correct; out of scope for this feature.
2. **Dynamic path** (low confidence) — The QueryBuilder LLM generates SQL from table metadata when no template matches. This path currently has three problems that degrade chat-window usability:
   - The LLM may `SELECT *` or select too many columns, producing tables that are unwieldy in the chat UI.
   - Columns that return entirely NULL/empty values add visual noise without information value.
   - The generation prompt provides only column `name` and `description`, omitting `data_type`, primary/foreign key info, and nullability — reducing the LLM's ability to select the right columns and construct correct joins.

Additionally, the dynamic path has two UX gaps compared to the template path:

- Generated SQL executes immediately with no user confirmation — unlike the template path's hypothesis-first clarification flow.
- When generation fails after retry, the error message is a raw validation dump with no actionable guidance.

This spec addresses five areas: generation-time column selectivity, post-execution result trimming, richer metadata in the generation prompt, a confidence gate before execution, and improved error recovery.

**Scope boundary**: Template-based queries are excluded. Those are implemented by developers/business users and work as-is. Parameterized execution for dynamic queries is also deferred — the existing QueryValidator + table allowlist provides sufficient security for LLM-generated SQL.

## User Scenarios & Testing

### User Story 1 — Selective Column Generation (Priority: P1)

The QueryBuilder LLM generates SQL with only the columns relevant to the user's question, rather than selecting all available columns.

**Why this priority**: This is the highest-leverage fix. Generating fewer columns at the SQL level reduces data transfer, speeds up query execution, and produces compact results without post-processing. It also reduces the chance of hitting the chat window's width constraint.

**Independent Test**: Ask "what are the top 10 customers by order count?" against a schema where `Sales.Customers` has 15+ columns. Verify the generated SQL selects only identity/name columns plus the aggregated order count — not all 15 columns.

**Acceptance Scenarios**:

1. **Given** a user question about a specific metric, **When** the QueryBuilder generates SQL, **Then** the SELECT clause contains at most `MAX_DISPLAY_COLUMNS` columns (configurable, default 8) unless the user explicitly asks for "all columns" or "full details"
2. **Given** a user question that mentions specific column names or attributes, **When** the QueryBuilder generates SQL, **Then** those columns are always included in the SELECT clause
3. **Given** a table with 20+ columns, **When** the QueryBuilder generates SQL, **Then** it prefers identity columns (name, ID), the metric/measure the user asked about, and columns referenced in WHERE/ORDER BY — dropping audit timestamps, internal IDs, and system fields
4. **Given** a user who explicitly says "show me all columns" or "full details", **When** the QueryBuilder generates SQL, **Then** the column limit is relaxed and all relevant columns are included

---

### User Story 2 — Empty Column Removal (Priority: P1)

After query execution, columns where every returned row has a NULL or empty-string value are stripped from the response before sending to the frontend.

**Why this priority**: Even a well-prompted LLM occasionally includes columns that are sparsely populated. Stripping fully-empty columns is a zero-risk post-processing step that always improves the display without losing information (there was none to lose).

**Independent Test**: Execute a dynamic query that returns 10 rows where 2 of 8 columns are entirely NULL. Verify the `NL2SQLResponse.columns` list and `sql_response` row dicts exclude those 2 columns.

**Acceptance Scenarios**:

1. **Given** query results where column X has NULL in every row, **When** the response is built, **Then** column X is excluded from `columns` and from every dict in `sql_response`
2. **Given** query results where column X has empty string (`""`) in every row, **When** the response is built, **Then** column X is excluded (treated same as all-NULL)
3. **Given** query results where column X has NULL in all but one row, **When** the response is built, **Then** column X is retained (partial data is still data)
4. **Given** query results where ALL columns have at least one non-null value, **When** the response is built, **Then** no columns are removed
5. **Given** query results from a template-based query, **When** the response is built, **Then** no column removal is applied (templates are curated and out of scope)

---

### User Story 3 — Post-Execution Column Capping with Overflow Indicator (Priority: P2)

When a dynamic query result still exceeds the display column limit after empty-column removal, the response caps visible columns and signals to the frontend that more are available.

**Why this priority**: This is the safety net for when the LLM ignores prompt guidance or when the user asks a broad question. It's lower than P1 because the generation-time prompt (US1) and empty-column removal (US2) will handle most cases. This catches the remainder.

**Independent Test**: Force a dynamic query that returns 12 non-empty columns. Verify the response contains only 8 visible columns, a `hidden_columns` list with the other 4, and row data for all 12 (so the frontend can expand without re-querying).

**Acceptance Scenarios**:

1. **Given** dynamic query results with more than `MAX_DISPLAY_COLUMNS` non-empty columns, **When** the response is built, **Then** `columns` contains the top N columns by relevance and `hidden_columns` contains the rest
2. **Given** the column cap applies, **When** selecting which columns to display, **Then** the system prioritizes: (a) columns mentioned in the user's question, (b) columns in GROUP BY / ORDER BY, (c) primary key or name columns, (d) columns appearing first in the SELECT clause
3. **Given** the column cap applies, **When** the response includes `hidden_columns`, **Then** full row data (all columns) is still included in `sql_response` so the frontend can expand without re-executing
4. **Given** a `hidden_columns` list is present, **When** the frontend renders the data table, **Then** it shows a "N more columns" indicator that the user can click to reveal all columns
5. **Given** dynamic query results with `MAX_DISPLAY_COLUMNS` or fewer non-empty columns, **When** the response is built, **Then** `hidden_columns` is empty and no indicator is shown
6. **Given** template-based query results, **When** the response is built, **Then** column capping is not applied (templates are curated)

---

### User Story 4 — Rich Column Metadata in Generation Prompt (Priority: P2)

The QueryBuilder generation prompt includes `data_type`, primary/foreign key flags, and foreign key references for each column, enabling the LLM to construct better joins and type-appropriate filters.

**Why this priority**: This improves SQL correctness for all dynamic queries — better joins, accurate WHERE clauses, type-safe comparisons. It's P2 because it improves quality broadly rather than solving the specific column-count problem, and the current prompt already works for simple queries.

**Independent Test**: Ask a question requiring a JOIN between `Sales.Orders` and `Sales.Customers`. Verify the generation prompt sent to the LLM includes FK references (`foreign_key_table`, `foreign_key_column`) for the join columns, and that the generated SQL uses the correct join condition.

**Acceptance Scenarios**:

1. **Given** table metadata with `data_type` populated, **When** `_build_generation_prompt()` formats the column info, **Then** each column entry includes `data_type` (e.g., `"int"`, `"nvarchar(100)"`, `"datetime2"`)
2. **Given** table metadata with `is_primary_key=True`, **When** the prompt is built, **Then** the column entry includes `"is_primary_key": true`
3. **Given** table metadata with `is_foreign_key=True` and `foreign_key_table`/`foreign_key_column` populated, **When** the prompt is built, **Then** the column entry includes the FK reference (e.g., `"foreign_key": "Sales.Customers.CustomerID"`)
4. **Given** table metadata with `is_nullable=False`, **When** the prompt is built, **Then** the column includes `"nullable": false` (helps the LLM avoid unnecessary NULL checks)
5. **Given** the enriched prompt, **When** the LLM generates a query requiring a JOIN, **Then** it uses the FK references to construct the correct join condition instead of guessing from column name similarity

---

### User Story 5 — Confidence-Gated Confirmation for Dynamic Queries (Priority: P2)

The QueryBuilder returns a self-assessed confidence score with each generated query. When confidence is low, the system shows the user a summary of what it plans to query and asks for confirmation before executing. High-confidence dynamic queries execute immediately — no friction added.

**Why this priority**: Dynamic queries are fully LLM-generated and have a higher risk of misinterpreting the user's intent than template-based queries. But requiring confirmation on every dynamic query would add unnecessary friction for straightforward questions. A confidence-gated approach balances safety with UX — only interrupting when the LLM itself is uncertain. P2 because the column refinements (US1–US3) address the most visible UX problem first.

**Independent Test**: Ask an ambiguous question like "show me the important purchase data" (vague intent, multiple plausible interpretations). Verify the QueryBuilder returns confidence < 0.7, and the system responds with a confirmation message like "I'll query Purchasing.PurchaseOrders showing supplier names and expected delivery dates. Run this query, or would you like to adjust?" with Accept/Revise options. Then ask a clear question like "show me the top 10 customers by order count" and verify it executes immediately without confirmation.

**Acceptance Scenarios**:

1. **Given** the QueryBuilder generates SQL, **When** it returns the response, **Then** the response includes a `confidence` field (0.0–1.0) reflecting how well the generated query matches the user's intent
2. **Given** a dynamic query with confidence ≥ 0.7 (high), **When** the query passes validation, **Then** it executes immediately without user confirmation
3. **Given** a dynamic query with confidence < 0.7 (low), **When** the query passes validation, **Then** the system presents a natural-language summary of the query intent (derived from the QueryBuilder's `reasoning` field) and offers Accept / Revise options before executing
4. **Given** the user accepts the confirmation, **When** the system receives the acceptance, **Then** it executes the SQL and returns results normally
5. **Given** the user chooses to revise, **When** the system receives the revision, **Then** it treats the user's follow-up as a refinement and re-routes to the QueryBuilder with the revised intent
6. **Given** a dynamic query on a refinement turn (the user already confirmed once in this conversation), **When** the refined query passes validation, **Then** the confirmation gate is skipped regardless of confidence — the user has already established intent
7. **Given** a dynamic query where the QueryBuilder's `reasoning` field is empty or missing, **When** the confirmation step triggers, **Then** the system falls back to showing a simplified summary derived from the SQL (tables + key columns + filter conditions)
8. **Given** a template-based query, **When** the query is ready for execution, **Then** no confirmation gate is applied (templates are pre-approved by design)
9. **Given** the QueryBuilder prompt, **When** it instructs the LLM to self-assess confidence, **Then** it provides guidance: high confidence (≥ 0.7) for clear single-table queries with explicit column references; low confidence (< 0.7) for ambiguous intent, multi-table queries with inferred joins, or vague filter conditions

---

### User Story 6 — Actionable Error Recovery (Priority: P3)

When dynamic SQL generation fails after validation retry, the system provides actionable guidance instead of dumping raw validation errors — suggesting how to narrow the question or showing what tables/columns are available.

**Why this priority**: This is the lowest-frequency scenario (fires only when the LLM fails twice), but when it does happen, the current error message is unhelpful. P3 because most users will never hit this path, and the other stories have higher impact.

**Independent Test**: Submit a question that deliberately triggers a validation failure (e.g., referencing a table not in the allowlist) and ensure the retry also fails. Verify the error message includes specific guidance like "Try asking about Sales, Purchasing, or Warehouse data" and lists 2–3 example questions.

**Acceptance Scenarios**:

1. **Given** a dynamic query that fails validation after the maximum retry count, **When** the error response is built, **Then** it includes a user-friendly message explaining the failure category (e.g., "I couldn't find the right tables for that question") without raw validation details
2. **Given** a validation failure related to disallowed tables, **When** the error response is built, **Then** it suggests the available schema areas (e.g., "I can help with Sales, Purchasing, Warehouse, and Application data")
3. **Given** a validation failure related to SQL syntax or structure, **When** the error response is built, **Then** it suggests the user try a simpler or more specific question (e.g., "Try narrowing your question to a specific table like Orders or Customers")
4. **Given** any dynamic query failure, **When** the error response is built, **Then** it includes 2–3 example questions relevant to the schema areas that were matched in the original table search
5. **Given** any dynamic query failure, **When** the error response includes example questions, **Then** those examples are rendered as clickable suggestion pills in the frontend (reusing the existing clarification options pattern)

---

### Edge Cases

- **Zero-row results**: If the query returns 0 rows, column stripping and capping are skipped — all columns are returned (there's nothing to evaluate for emptiness, and showing the column structure is informative).
- **Single-column result**: If the query returns exactly 1 column, it is never stripped or hidden, even if all values are NULL — the user should see the empty result.
- **All columns empty**: If every column in every row is NULL, return the original column list unmodified. An all-empty table is a meaningful result ("no data matched your query") and stripping everything would leave a confusing empty frame.
- **Very long column names**: Column names exceeding 40 characters should be truncated with `…` in the `hidden_columns` indicator text but preserved in full in the data.
- **Column order preservation**: The relative order of columns in the query result must be preserved after stripping/capping. Columns should not be reordered.
- **Confirmation on timeout**: If the user doesn't respond to a confirmation gate within the SSE connection lifetime, the query is not executed. The next user message in the thread will be treated as a fresh question or refinement.
- **Confirmation and empty results interaction**: If the user confirms a query and it returns zero rows, that is a valid result — no additional confirmation is needed.
- **Error recovery with no table matches**: If the original table search returned zero results, the error fallback cannot suggest schema-specific examples. It should fall back to generic guidance ("Try asking about specific business entities like customers, orders, or products").

## Requirements

### Functional Requirements

- **FR-001**: The QueryBuilder prompt MUST instruct the LLM to limit SELECT clauses to at most `MAX_DISPLAY_COLUMNS` (default 8) relevant columns unless the user explicitly requests all columns
- **FR-002**: The `_build_generation_prompt()` function MUST include `data_type`, `is_primary_key`, `is_foreign_key`, `foreign_key_table`, `foreign_key_column`, and `is_nullable` for each column in the prompt
- **FR-003**: A post-execution column filter MUST remove columns where every row value is NULL or empty string, for dynamic query results only
- **FR-004**: A post-execution column cap MUST limit visible columns to `MAX_DISPLAY_COLUMNS` when the non-empty column count exceeds the limit, for dynamic query results only
- **FR-005**: When columns are capped, the response MUST include a `hidden_columns: list[str]` field listing the names of hidden columns
- **FR-006**: When columns are capped, `sql_response` MUST still contain all column data (visible + hidden) so the frontend can reveal hidden columns without re-executing
- **FR-007**: Column capping MUST prioritize: (a) columns mentioned in the user's question, (b) columns in GROUP BY / ORDER BY / aggregate expressions, (c) primary key or identity columns, (d) positional order in SELECT
- **FR-008**: The frontend data table MUST render a "Show N more columns" control when `hidden_columns` is non-empty
- **FR-009**: Clicking the "Show more columns" control MUST reveal the hidden columns from the existing data without a new backend request
- **FR-010**: `MAX_DISPLAY_COLUMNS` MUST be configurable (environment variable or constant) with a default of 8
- **FR-011**: Column stripping and capping MUST NOT apply to template-based query results (`query_source="template"`)
- **FR-012**: Column stripping and capping MUST preserve the original column order from the query result
- **FR-013**: The QueryBuilder LLM MUST return a self-assessed `confidence` score (0.0–1.0) with each generated query
- **FR-014**: Dynamic queries with confidence ≥ 0.7 MUST execute immediately without user confirmation
- **FR-015**: Dynamic queries with confidence < 0.7 MUST present a natural-language summary of the query intent and offer Accept / Revise options before executing
- **FR-016**: The confirmation summary MUST be derived from the QueryBuilder's `reasoning` field, with a fallback to SQL-derived summary (tables, key columns, filters) when reasoning is unavailable
- **FR-017**: Accepting the confirmation MUST execute the query; choosing Revise MUST re-route to QueryBuilder with the user's revised intent
- **FR-018**: Refinement turns (user already confirmed once in this conversation) MUST skip the confirmation gate regardless of confidence
- **FR-019**: The confirmation gate MUST NOT apply to template-based queries
- **FR-020**: When dynamic query generation fails after max retries, the error response MUST include a user-friendly failure category, NOT raw validation details
- **FR-021**: Error responses for disallowed-table failures MUST suggest available schema areas
- **FR-022**: Error responses for any dynamic query failure MUST include 2–3 contextual example questions rendered as clickable suggestion pills
- **FR-023**: When table search returned zero results, error fallback MUST provide generic guidance rather than schema-specific suggestions

### Key Entities

- **NL2SQLResponse** (modified): Gains `hidden_columns: list[str]` field; gains `query_summary: str` and `query_confidence: float` fields for the confidence gate; gains `error_suggestions: list[str]` for actionable recovery
- **SQLDraft** (modified): Gains `confidence: float` field populated by QueryBuilder
- **QueryBuilder prompt** (modified): Enhanced column metadata and selectivity instructions
- **Column filter** (new): Pure function that strips empty columns and caps visible columns; located in a shared utility for testability
- **DataTable component** (modified): Renders hidden-columns indicator and expand control
- **Confirmation gate** (new): Logic in NL2SQLController that intercepts validated dynamic queries before execution, returning a confirmation response to the orchestrator
- **Error recovery** (modified): Enhanced error-building logic in NL2SQLController that classifies failure type and generates actionable suggestions

## Success Criteria

### Measurable Outcomes

- **SC-001**: Dynamic queries against tables with 15+ columns produce results with ≤ 8 visible columns in ≥ 80% of cases (measured by prompt improvement alone, before post-processing)
- **SC-002**: All-NULL columns are never displayed in dynamic query results
- **SC-003**: No regression in template-based query results (templates pass through unmodified)
- **SC-004**: Column expansion in the frontend works without a backend round-trip (client-side toggle from existing data)
- **SC-005**: Low-confidence (< 0.7) dynamic queries present a confirmation summary before execution in 100% of cases
- **SC-006**: High-confidence (≥ 0.7) dynamic queries execute immediately without confirmation in 100% of cases
- **SC-007**: Failed dynamic queries never display raw validation errors to the user
- **SC-008**: Failed dynamic queries include at least 2 actionable example questions in 100% of cases
- **SC-009**: Quality checks (`uv run poe check`) pass with zero errors/warnings
- **SC-010**: Unit tests cover column stripping, capping, relevance ranking, confirmation gating, error recovery classification, and edge cases (zero rows, single column, all empty, no table matches)
