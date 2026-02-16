# Feature Specification: NL2SQL Confidence Scoring, Dynamic Allowed Values, Schema-Area Context, and Clarification Flows

**Feature Branch**: `nl2sql-confidence-and-clarification`
**Created**: 2026-02-15
**Status**: Draft
**Input**: Design analysis of current NL2SQL workflow gaps + external UX research document

## User Scenarios & Testing

### User Story 1 - Hypothesis-First Clarification (Priority: P1)

When the system cannot confidently extract a parameter, it presents its best guess and asks the user to confirm or choose an alternative — instead of asking open-ended questions.

**Why this priority**: This is the highest-impact UX improvement. Current clarification prompts are open-ended ("What value do you want?") which breaks user momentum. The document's core principle — "Never reset the user" — depends on this.

**Independent Test**: Send an ambiguous query like "show me top products" where the ORDER parameter (quantity vs. revenue) is unclear. Verify the system responds with a hypothesis ("It looks like you want top products by quantity sold. Is that right, or did you mean by revenue?") rather than an open question.

**Acceptance Scenarios**:

1. **Given** a query where a parameter has confidence 0.6–0.85, **When** the parameter extractor returns its best guess, **Then** the system applies the guess and confirms in the response text ("Showing top 10 by quantity — is that right?")
2. **Given** a query where a parameter has confidence < 0.6, **When** the system needs clarification, **Then** it presents its best guess plus 1–2 alternatives as a choice, not an open question
3. **Given** a query where a parameter has an exact match to allowed_values, **When** the parameter extractor resolves it, **Then** no clarification is needed (confidence 1.0, auto-applied silently)
4. **Given** a clarification response from the user, **When** the user says "yes" or picks an alternative, **Then** the system proceeds without re-extracting already-confirmed parameters

---

### User Story 2 - Deterministic Confidence Scoring (Priority: P1)

The system computes a per-parameter confidence score based on how the parameter was resolved, enabling threshold-gated actions (auto-apply, confirm, or ask).

**Why this priority**: This is the enabler for User Story 1. Without confidence scores, the system can't decide when to auto-apply vs. confirm vs. ask. The `confidence_weight` field already exists but is unused.

**Independent Test**: Submit a query that matches a template with 3 parameters. Verify the extraction response includes per-parameter confidence scores, and that the NL2SQL controller routes correctly based on the confidence thresholds.

**Acceptance Scenarios**:

1. **Given** a parameter resolved by exact match to `allowed_values`, **When** extraction completes, **Then** its confidence is 1.0
2. **Given** a parameter resolved by fuzzy match via `_fuzzy_match_allowed_value()`, **When** extraction completes, **Then** its confidence is 0.85
3. **Given** a parameter resolved by applying a default value, **When** extraction completes, **Then** its confidence is 0.7
4. **Given** a parameter resolved by LLM extraction, **When** the value passes validation, **Then** its confidence is 0.75; when it fails validation, **Then** its confidence is 0.3
5. **Given** an effective confidence above 0.85 for all parameters, **When** the NL2SQL controller processes the draft, **Then** it executes without any confirmation prompt
6. **Given** an effective confidence between 0.6–0.85 for at least one parameter, **When** the NL2SQL controller processes the draft, **Then** it executes but includes a confirmation note in the response
7. **Given** an effective confidence below 0.6 for any parameter, **When** the NL2SQL controller processes the draft, **Then** it triggers a clarification flow before execution

---

### User Story 3 - Schema-Area Contextual Suggestions (Priority: P2)

After returning query results, the system suggests relevant follow-up questions based on which schema area (Sales, Purchasing, Warehouse, Application) the user is exploring.

**Why this priority**: Improves discoverability and guides users deeper into relevant data. Lower priority than confidence/clarification because the system works without it — it just works better with it.

**Independent Test**: Ask a sales-related question (e.g., "show me top customers"). Verify the response includes contextual suggestions rendered as clickable pills (same pattern as clarification options) like "Explore order trends" or "Drill into invoice details."

**Frontend Approach**: Extend the existing `makeAssistantToolUI` component (`NL2SQLToolUI` in `src/frontend/components/assistant-ui/nl2sql-tool-ui.tsx`). The backend adds a `suggestions` field to the `NL2SQLResult` tool response. The frontend renders these as clickable pills below the Observations section — reusing the same `threadRuntime.composer.setText()` + `.send()` pattern already used by `ClarificationOptions`. This keeps suggestions contextually anchored to the query result that generated them.

**Why not `SuggestionPrimitive`**: The assistant-ui `Suggestions()` API and `ThreadPrimitive.Suggestions` are designed for empty-thread welcome screens, not post-response follow-ups. They render at the thread level (not inside a tool result), would require migrating to the new `useAui` runtime pattern, and don't anchor suggestions to specific query results.

**Acceptance Scenarios**:

1. **Given** a query that uses Sales tables, **When** the `nl2sql_query` tool result is rendered, **Then** it displays 2–3 relevant follow-up suggestions as clickable pills from the Sales domain
2. **Given** a query that spans Sales and Purchasing tables, **When** the tool result is rendered, **Then** it includes a cross-domain suggestion pill ("Want to see the supplier side?")
3. **Given** 3+ consecutive queries in the same schema area, **When** the tool result is rendered, **Then** it includes one cross-area suggestion pill to broaden the analysis
4. **Given** an empty result set in the Sales area, **When** the orchestrator renders the recovery message, **Then** the suggested alternatives are contextual to the Sales schema (e.g., "Try a different date range" or "Check invoice data instead")
5. **Given** a user clicks a suggestion pill, **When** the click fires, **Then** it populates the composer with the suggestion prompt and auto-sends it

---

### User Story 4 - Dynamic Allowed Values Cache (Priority: P3)

For parameters whose valid values change in the database (e.g., customer names, city names), the system loads and caches allowed values at runtime instead of relying on static lists in query templates.

**Why this priority**: Important for production readiness but not critical for the core UX flow. The system works with static values today — this makes it work with dynamic data.

**Independent Test**: Configure a parameter with `allowed_values_source: "database"` pointing to a column. Verify the system queries the database for distinct values, caches them, and uses them for fuzzy matching during parameter extraction.

**Acceptance Scenarios**:

1. **Given** a parameter with `allowed_values_source: "database"` and a valid `column` mapping, **When** the parameter extractor processes it, **Then** it resolves allowed values from an in-memory cache (not the template JSON)
2. **Given** a cache miss for a `(table, column)` pair, **When** the parameter extractor needs the values, **Then** it executes `SELECT DISTINCT column FROM table` and caches the result with a configurable TTL
3. **Given** cached values older than the TTL, **When** the parameter extractor needs the values, **Then** it refreshes the cache in the background and uses stale values for the current request (stale-while-revalidate)
4. **Given** a column with more than 500 distinct values, **When** the cache loads, **Then** it caps at 500 values and logs a warning; the parameter extractor falls back to LLM extraction without fuzzy matching for that parameter
5. **Given** a parameter with `allowed_values_source: null`, **When** the parameter extractor processes it, **Then** it uses `validation.allowed_values` from the template as today (no behavior change)

---

### Edge Cases

- What happens when the database is unreachable during cache population? → Fall back to LLM-only extraction, log a warning, retry on next request.
- What happens when a user contradicts a confirmed parameter? → Re-extract that parameter from scratch, keep all others.
- What happens when confidence is exactly at a threshold boundary (e.g., 0.85)? → Treat as the higher tier (≥ 0.85 → auto-apply).
- What happens when all parameters are auto-applied but the query returns empty results? → Use the "controlled backtracking" pattern from the orchestrator (suggest relaxing constraints).
- What happens when schema-area detection is ambiguous (e.g., Application.People used in a Sales context)? → Prioritize the primary table's schema (the FROM table, not JOINed lookup tables).
- What happens when a database-sourced parameter has `validation.allowed_values` set in the template JSON? → Treat as a configuration error. Log a warning and ignore the static list — `allowed_values_source: "database"` takes precedence. The hydration step overwrites `validation.allowed_values` regardless.

## Requirements

### Functional Requirements

- **FR-001**: System MUST compute a confidence score (0.0–1.0) for each extracted parameter based on its resolution method
- **FR-002**: System MUST use the `confidence_weight` field from `ParameterDefinition` in the effective confidence calculation. Default weight is 1.0 (pass-through); values < 1.0 force parameters into lower confidence tiers for critical fields
- **FR-003**: System MUST apply threshold-gated routing: ≥0.85 auto-apply, 0.6–0.85 apply+confirm, <0.6 ask
- **FR-004**: `MissingParameter` model MUST include `best_guess`, `guess_confidence`, and `alternatives` fields
- **FR-005**: Clarification prompts MUST use hypothesis-first format (present best guess + alternatives)
- **FR-006**: System MUST enforce single-question-per-turn for clarification flows
- **FR-007**: System MUST track `current_schema_area` in `ConversationContext` based on the tables used
- **FR-008**: Backend MUST include schema-contextual follow-up suggestions in the `NL2SQLResult` tool response as a `suggestions` field
- **FR-009**: Frontend `NL2SQLToolUI` MUST render suggestions as clickable pills that auto-send the suggestion prompt to the composer (reusing the `ClarificationOptions` pattern)
- **FR-010**: `ParameterDefinition` MUST support `allowed_values_source` (`"database"` or `null`) and `table: str | None` fields. When `allowed_values_source` is `"database"`, both `table` and `column` must be set. The template's `validation.allowed_values` MUST be `null` for database-sourced parameters — values are hydrated at runtime.
- **FR-011**: System MUST provide an `AllowedValuesProvider` that caches `SELECT DISTINCT` results with configurable TTL
- **FR-012**: Cache MUST cap per-column values at a configurable limit (default 500)
- **FR-013**: System MUST NOT block on cache misses — use stale-while-revalidate pattern
- **FR-014**: System MUST preserve previously extracted parameters during clarification turns (never re-extract confirmed parameters)
- **FR-015**: `validation.allowed_values` in query templates MUST be reserved for **static enums** (SQL keywords like `"ASC"`/`"DESC"`, fixed structural choices). Parameters whose allowed values come from database data MUST use `allowed_values_source: "database"` instead, with `validation.allowed_values: null` in the template JSON.

### Key Entities

- **ParameterConfidence**: Represents the confidence score for a single extracted parameter, including resolution method and effective score
- **AllowedValuesProvider**: Singleton service that manages the in-memory cache of dynamic allowed values
- **SchemaAreaContext**: Tracks which database schema area the user is exploring and provides domain-specific suggestions
- **SchemaSuggestion**: Pydantic model for a single suggestion — `title: str`, `prompt: str` — included in the `NL2SQLResult` tool response

## Success Criteria

### Measurable Outcomes

- **SC-001**: Clarification prompts always present a hypothesis (best guess) — zero open-ended "what do you want?" questions
- **SC-002**: Parameters resolved via exact match or fuzzy match skip the LLM call (existing fast path preserved)
- **SC-003**: Dynamic allowed values cache loads in < 500ms per column and has < 1% miss rate after warm-up
- **SC-004**: Schema-area suggestions appear as clickable pills in > 90% of query responses where `NL2SQLResponse.error is None` (includes empty-result responses)
- **SC-005**: No regressions in existing parameter extraction — all current test scenarios continue to pass
