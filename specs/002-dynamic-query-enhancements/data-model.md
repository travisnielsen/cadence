# Data Model: Dynamic Query Enhancements

## Modified Models

### SQLDraft (src/backend/models/generation.py)

Existing model. New field:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `confidence` | `float` | `0.0` | QueryBuilder self-assessed confidence (0.0–1.0) |

### NL2SQLResponse (src/backend/models/execution.py)

Existing model. New fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `hidden_columns` | `list[str]` | `[]` | Column names hidden by the display cap |
| `query_summary` | `str` | `""` | Natural-language summary for confidence gate confirmation |
| `query_confidence` | `float` | `0.0` | QueryBuilder confidence score passed through for frontend |
| `error_suggestions` | `list[SchemaSuggestion]` | `[]` | Actionable example questions on error (rendered as pills) |

Note: `error_suggestions` uses the existing `SchemaSuggestion` model (`title` + `prompt`). It's a separate field from `suggestions` to distinguish post-result suggestions from error recovery suggestions.

### NL2SQLResult (frontend TypeScript interface)

Existing interface in `nl2sql-tool-ui.tsx`. New fields:

| Field | Type | Optional | Description |
|-------|------|----------|-------------|
| `hidden_columns` | `string[]` | Yes | Column names hidden by display cap |
| `query_summary` | `string` | Yes | Confirmation summary text |
| `query_confidence` | `number` | Yes | 0.0–1.0 confidence |
| `error_suggestions` | `SchemaSuggestion[]` | Yes | Error recovery pill suggestions |

## New Models

### ColumnRefinementResult (src/backend/entities/shared/column_filter.py)

Frozen dataclass (not Pydantic — internal only, like `ParameterizedQuery`):

| Field | Type | Description |
|-------|------|-------------|
| `columns` | `list[str]` | Visible column names (after stripping + capping) |
| `hidden_columns` | `list[str]` | Column names hidden by the cap |
| `rows` | `list[dict]` | Original row data (all columns preserved) |

## Unchanged Models

- **QueryBuilderRequest** — No changes; tables, user_query, retry_count are sufficient
- **QueryBuilderRequestMessage** — No changes
- **SQLDraftMessage** — No changes (carries JSON-serialized SQLDraft)
- **SchemaSuggestion** — No changes; reused for error recovery
- **ClarificationInfo** — No changes; reused for confirmation gate via existing pattern
- **TableColumn** / **TableMetadata** — No changes to model; hydration function starts populating existing fields

## State Transitions

### Confidence Gate Flow

```
QueryBuilder generates SQL + confidence
  → NL2SQLController receives SQLDraft
    → QueryValidator validates
      → confidence >= 0.7: Execute SQL → NL2SQLResponse (results)
      → confidence < 0.7:  Return NL2SQLResponse (needs_confirmation=True, query_summary, no results)
        → User accepts: Execute SQL → NL2SQLResponse (results)
        → User revises: Re-route to QueryBuilder with revised intent
```

### Column Refinement Flow

```
SQL execution returns rows + columns
  → refine_columns(columns, rows, user_query, max_cols)
    → Strip empty columns
    → If count > max_cols: rank by relevance, cap, set hidden_columns
  → NL2SQLResponse built with refined columns + hidden_columns + full rows
```
