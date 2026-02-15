# Implementation Plan: NL2SQL Confidence Scoring, Dynamic Allowed Values, Schema-Area Context, and Clarification Flows

**Branch**: `nl2sql-confidence-and-clarification` | **Date**: 2026-02-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/nl2sql-confidence-and-clarification/spec.md`

## Summary

Enhance the NL2SQL workflow with per-parameter confidence scoring, hypothesis-first clarification UX, schema-area contextual suggestions, and a dynamic allowed-values cache. These four capabilities work together to implement the "progressive intent narrowing" pattern — never resetting the user, always carrying forward partial understanding.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Microsoft Agent Framework (MAF), FastAPI, Pydantic, Azure AI Search, Azure SQL (pyodbc)
**Storage**: Azure SQL (for dynamic allowed values), Azure AI Search (for template/table metadata)
**Testing**: pytest + pytest-asyncio
**Target Platform**: Linux container (Azure Container App)
**Performance Goals**: Cache loads < 500ms per column, no added latency for deterministic confidence scoring
**Constraints**: Async-first (no blocking I/O), all data boundaries use Pydantic models, single-responsibility executors
**Scale/Scope**: ~30 table definitions, ~8 query templates, hundreds of potential allowed values per column

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| Async-First | ✅ Pass | Cache refresh uses async DB calls, no blocking |
| Validated Data at Boundaries | ✅ Pass | New fields added to existing Pydantic models |
| Fully Typed | ✅ Pass | All new functions fully typed |
| Single-Responsibility Executors | ✅ Pass | AllowedValuesProvider is a standalone service, confidence scoring stays in parameter extractor |
| Automated Quality Gates | ✅ Pass | All changes must pass `uv run poe check` |

## Project Structure

### Documentation (this feature)

```text
specs/nl2sql-confidence-and-clarification/
├── spec.md              # Feature specification
├── plan.md              # This file
└── tasks.md             # Implementation tasks
```

### Source Code Changes

```text
src/backend/
├── models/
│   ├── schema.py              # MODIFY: Add allowed_values_source to ParameterDefinition
│   └── extraction.py          # MODIFY: Add confidence fields to MissingParameter, new ParameterConfidence model
├── entities/
│   ├── parameter_extractor/
│   │   ├── executor.py        # MODIFY: Add confidence scoring to _pre_extract_parameters(), update LLM parsing
│   │   └── prompt.md          # MODIFY: Update prompt to return confidence + best_guess in clarification
│   ├── nl2sql_controller/
│   │   └── executor.py        # MODIFY: Route based on confidence thresholds, update clarification prompt format
│   ├── orchestrator/
│   │   ├── orchestrator.py    # MODIFY: Add schema_area tracking to ConversationContext, add suggestion generation
│   │   └── orchestrator_prompt.md  # MODIFY: Add schema-area suggestion instructions
│   └── shared/
│       └── allowed_values_provider.py  # NEW: Dynamic allowed values cache
│   └── workflow/
│       └── workflow.py        # MODIFY: Initialize AllowedValuesProvider singleton alongside existing clients

tests/
├── unit/
│   ├── test_confidence_scoring.py         # NEW: Tests for deterministic confidence
│   ├── test_allowed_values_provider.py    # NEW: Tests for cache behavior
│   ├── test_clarification_format.py       # NEW: Tests for hypothesis-first prompts
│   └── test_schema_area_context.py        # NEW: Tests for schema area detection + suggestions

src/frontend/components/assistant-ui/
├── nl2sql-tool-ui.tsx             # MODIFY: Add SuggestionPills component, render after Observations
```

**Structure Decision**: All changes are within the existing `src/backend/` structure. One new file (`allowed_values_provider.py`) in `entities/shared/`. All other changes are modifications to existing files. Tests follow existing `tests/unit/` convention.

## Design Decisions

### D1: Confidence Scoring Is Deterministic (Not LLM-Based)

**Decision**: Compute confidence from the resolution method rather than asking the LLM to self-assess.

**Rationale**:

- No added LLM latency
- Predictable, testable behavior
- The `_pre_extract_parameters()` fast path already classifies how each parameter was resolved
- LLM self-assessment can be layered on later if needed

**Confidence Score Table**:

| Resolution Method | Base Confidence | Notes |
|---|---|---|
| Exact match to `allowed_values` | 1.0 | Case-insensitive exact match |
| Fuzzy match via `_fuzzy_match_allowed_value()` | 0.85 | Substring/pluralization match |
| LLM extraction, passes validation | 0.75 | LLM inferred, validated |
| Default value applied (`default_value`) | 0.7 | System assumed, user didn't say it |
| Default policy computed (`default_policy`) | 0.7 | Computed default (e.g., current_date) |
| LLM extraction, no validation rule | 0.65 | LLM inferred, no way to validate |
| LLM extraction, fails validation | 0.3 | LLM guess was wrong |

**Effective confidence calculation**:

```
effective_confidence = base_confidence * max(confidence_weight, 0.3)
```

Where `confidence_weight` comes from `ParameterDefinition`. Default is **1.0** (pass-through), meaning base confidence = effective confidence for most parameters. Setting weight < 1.0 on specific critical parameters forces them into lower tiers even on good matches — e.g., a date-range parameter with `confidence_weight: 0.6` would need an exact match (1.0 × 0.6 = 0.6) to reach the confirm tier. The floor of 0.3 prevents templates with `confidence_weight: 0.0` from zeroing out all scores.

**Example effective scores (with default weight 1.0)**:

| Resolution Method | Base | Effective | Tier |
|---|---|---|---|
| Exact match | 1.0 | 1.0 | Auto-apply |
| Fuzzy match | 0.85 | 0.85 | Auto-apply |
| LLM validated | 0.75 | 0.75 | Confirm |
| Default value | 0.7 | 0.7 | Confirm |
| LLM unvalidated | 0.65 | 0.65 | Confirm |
| LLM failed | 0.3 | 0.3 | Ask |

**Example with critical parameter (weight 0.6)**:

| Resolution Method | Base | Effective | Tier |
|---|---|---|---|
| Exact match | 1.0 | 0.6 | Confirm (not auto) |
| Fuzzy match | 0.85 | 0.51 | Ask |
| LLM validated | 0.75 | 0.45 | Ask |

### D2: Threshold-Gated Routing in NL2SQL Controller

**Decision**: The NL2SQL controller receives confidence scores per parameter in the `SQLDraft` and routes accordingly.

| Min Effective Confidence | Action |
|---|---|
| ≥ 0.85 | Auto-apply silently → execute SQL |
| 0.6–0.85 | Apply and execute, but include confirmation text in response |
| < 0.6 | Trigger hypothesis-first clarification before execution |

The `SQLDraft` model gains a new `parameter_confidences: dict[str, float]` field. The controller checks the minimum confidence across all parameters to decide the action.

### D3: Hypothesis-First Clarification Format

**Decision**: Enrich `MissingParameter` with `best_guess`, `guess_confidence`, and `alternatives` so the NL2SQL controller can format hypothesis-first prompts.

**Prompt template**:

```
"It looks like you want {best_guess_description}. Is that correct, or did you mean {alternative_1} or {alternative_2}?"
```

For parameters with `allowed_values`, alternatives come from the values list (excluding the best guess). For parameters without allowed values, alternatives come from the LLM's extraction response.

### D4: Schema Area Detection + Frontend Suggestion Pills via `makeAssistantToolUI`

**Decision**: Infer `current_schema_area` from the fully qualified table names (e.g., `Sales.Orders` → `"sales"`). Use the FROM clause's primary table, not JOINed lookup tables. Deliver suggestions through the existing `makeAssistantToolUI` pattern, not through `SuggestionPrimitive`.

**Why `makeAssistantToolUI` (not `SuggestionPrimitive`)**: The codebase already uses `makeAssistantToolUI` for the `NL2SQLToolUI` component, which renders data tables, SQL sections, observations, and — critically — `ClarificationOptions` (clickable pills that populate + send the composer). The `Suggestions()` API / `ThreadPrimitive.Suggestions` is purpose-built for empty-thread welcome screens, not post-response follow-ups. Extending the existing tool UI keeps suggestions anchored to the query result that generated them.

**Full-stack data flow**:

1. **Backend** — Orchestrator detects schema area from the tables in the query result, selects 2–3 suggestions from `SCHEMA_SUGGESTIONS`, and includes them as `suggestions: list[SchemaSuggestion]` in the `NL2SQLResult` tool response
2. **Frontend** — `NL2SQLToolUI` renders a `<SuggestionPills>` section after the Observations block, using the same `threadRuntime.composer.setText(prompt)` + `.send()` pattern as `ClarificationOptions`
3. **Styling** — Differentiated from clarification pills (neutral/green border instead of blue, "You might also explore:" header)

**Backend model addition** (`src/backend/models/execution.py`):

```python
class SchemaSuggestion(BaseModel):
    """A contextual follow-up suggestion anchored to a query result."""
    title: str = Field(description="Short display label, e.g. 'Explore order trends'")
    prompt: str = Field(description="Full query to send when clicked, e.g. 'Show me order trends over the last 6 months'")
```

Added to `NL2SQLResponse.suggestions: list[SchemaSuggestion] = []`.

**Frontend component** (`src/frontend/components/assistant-ui/nl2sql-tool-ui.tsx`):

```tsx
function SuggestionPills({ suggestions }: { suggestions: SchemaSuggestion[] }) {
  const threadRuntime = useThreadRuntime();
  const handleClick = useCallback((prompt: string) => {
    threadRuntime.composer.setText(prompt);
    threadRuntime.composer.send();
  }, [threadRuntime]);

  return (
    <div className="mt-3 pt-3 border-t">
      <p className="text-sm font-medium mb-2">You might also explore:</p>
      <div className="flex flex-wrap gap-2">
        {suggestions.map((s) => (
          <button key={s.title} onClick={() => handleClick(s.prompt)}
            className="px-3 py-1.5 text-sm bg-white dark:bg-gray-800 border border-green-200
            dark:border-green-700 rounded-full text-green-700 dark:text-green-300
            hover:bg-green-100 dark:hover:bg-green-900 cursor-pointer transition-colors">
            {s.title}
          </button>
        ))}
      </div>
    </div>
  );
}
```

**Schema area → suggestion map** is maintained as a static dict in the orchestrator, not in the database:

```python
SCHEMA_SUGGESTIONS: dict[str, list[SchemaSuggestion]] = {
    "sales": [
        SchemaSuggestion(title="Order trends", prompt="Show me order trends over the last 6 months"),
        SchemaSuggestion(title="Invoice details", prompt="Drill into invoice line items for the most recent orders"),
        SchemaSuggestion(title="Customer categories", prompt="Compare total revenue across customer buying groups"),
        SchemaSuggestion(title="Special deals", prompt="Show active special deals and their discount percentages"),
    ],
    "purchasing": [
        SchemaSuggestion(title="PO status", prompt="Track purchase order status and expected delivery dates"),
        SchemaSuggestion(title="Supplier performance", prompt="Analyze supplier categories and order volumes"),
        SchemaSuggestion(title="Supplier transactions", prompt="Review recent supplier transaction history"),
    ],
    "warehouse": [
        SchemaSuggestion(title="Stock levels", prompt="Check current stock levels and holdings across warehouses"),
        SchemaSuggestion(title="Stock categories", prompt="Explore stock groups and item categories"),
        SchemaSuggestion(title="Stock transactions", prompt="Review stock transaction history for the last 30 days"),
        SchemaSuggestion(title="Package types", prompt="Analyze color and package type distributions for stock items"),
    ],
    "application": [
        SchemaSuggestion(title="People & contacts", prompt="Look up people, their roles, and contact information"),
        SchemaSuggestion(title="Geographic data", prompt="Explore cities, states, and countries in the system"),
        SchemaSuggestion(title="Delivery methods", prompt="Review available delivery and payment methods"),
    ],
}
```

### D5: Allowed Values Cache Design

**Decision**: A singleton `AllowedValuesProvider` with async stale-while-revalidate pattern.

**SQL Client**: Reuses `AzureSqlClient` from `src/backend/entities/shared/clients/sql_client.py` — the same async context manager used by the `execute_sql` tool. No new SQL connection logic.

**Cache key**: `(schema.table, column)` — e.g., `("Sales.CustomerCategories", "CustomerCategoryName")`
**Cache value**: `list[str]` (distinct values, capped at `MAX_CACHED_VALUES`)
**TTL**: Configurable, default 10 minutes
**Population**: Lazy on first access via `SELECT DISTINCT {column} FROM {table} ORDER BY {column}`
**Refresh**: Background async task triggered when TTL expires; stale values served until refresh completes
**Max values**: 500 per column (configurable). If exceeded, store the 500 values but flag the parameter as "partial cache" so the extractor knows not to enforce strict matching.

**Template schema for database-sourced parameters**: The `ParameterDefinition` gains a `table: str | None` field alongside the existing `column: str | None`. For `allowed_values_source: "database"`, both `table` and `column` must be set, and `validation.allowed_values` must be `null` in the template JSON (values are hydrated at runtime).

**`validation.allowed_values` usage rules**:

| Parameter pattern | `allowed_values_source` | `validation.allowed_values` | Example |
|---|---|---|---|
| **Structural enum** (SQL keywords) | `null` | Static list in template | `order`: `["ASC", "DESC"]` |
| **Database lookup** (data values) | `"database"` | `null` in template, hydrated at runtime | `category_name`: hydrated from `Sales.CustomerCategories.CustomerCategoryName` |
| **Free-form** (numbers, dates, text) | `null` | `null` | `count`: validated by type/min/max only |

This separation makes templates self-documenting: if `validation.allowed_values` is set, the values are permanent structural constraints. If `allowed_values_source` is `"database"`, values come from the DB and the validation block only specifies type/range/regex constraints.

Example database-sourced parameter:

```json
{
    "name": "category_name",
    "column": "CustomerCategoryName",
    "table": "Sales.CustomerCategories",
    "allowed_values_source": "database",
    "confidence_weight": 1.0,
    "validation": { "type": "string" }
}
```

Example structural enum parameter (unchanged):

```json
{
    "name": "order",
    "column": null,
    "allowed_values_source": null,
    "confidence_weight": 1.0,
    "validation": { "type": "string", "allowed_values": ["ASC", "DESC"] }
}
```

**Integration — Hydrate Once, Use Everywhere**: The provider is instantiated once at app startup (alongside the `AzureAIClient` singletons in `workflow.py`) and passed to the parameter extractor executor. At the **start** of each extraction request, the extractor calls `provider.get_allowed_values(param.table, param.column)` for each database-sourced parameter and **sets the result on `param.validation.allowed_values`**. This single hydration step means all downstream consumers work unchanged:

| Consumer | What it reads | Impact |
|----------|--------------|--------|
| `_fuzzy_match_allowed_value()` | `param.validation.allowed_values` | ✅ No change — fuzzy match works on hydrated values |
| `_build_extraction_prompt()` | `param.validation.allowed_values` | ✅ No change — LLM prompt includes hydrated values |
| `ParameterValidator._validate_string()` | `param.validation.allowed_values` | ✅ No change — strict match works on hydrated values |

**Partial cache handling**: When a column has > 500 distinct values, the provider returns `is_partial=True`. The extractor sets a flag so the validator **skips** strict `allowed_values` matching for that parameter (falls back to LLM extraction without constraint). This prevents false validation failures for high-cardinality columns.

**Mutation scope**: The hydration mutates the in-memory `ParameterDefinition` for the current request only. Since `QueryTemplate` objects are loaded fresh from the search index per request (via `AzureSearchClient`), there's no risk of stale mutation across requests.

## Complexity Tracking

No constitution violations. All changes fit within existing patterns:

- New Pydantic fields on existing models
- New standalone service class (AllowedValuesProvider)
- Prompt engineering changes
- Routing logic changes in existing executor handlers
