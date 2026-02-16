# Quickstart: Dynamic Query Enhancements

## Setup

No new dependencies required. All changes use existing libraries (Pydantic, FastAPI, React).

```bash
# Ensure environment is active
cd /home/trniel/cadence
source .venv/bin/activate

# Run existing tests to verify baseline
uv run poe check
uv run poe test
```

## Configuration

| Variable | Default | Location | Description |
|----------|---------|----------|-------------|
| `MAX_DISPLAY_COLUMNS` | `8` | `entities/shared/column_filter.py` | Max visible columns for dynamic queries |
| `DYNAMIC_CONFIDENCE_THRESHOLD` | `0.7` | `entities/nl2sql_controller/executor.py` | Below this, confirmation gate triggers |

## Files to Modify (by priority)

### P1 — Column Selectivity & Empty Removal

| File | Change |
|------|--------|
| `entities/query_builder/prompt.md` | Add column selectivity instructions + `confidence` JSON field |
| `entities/query_builder/executor.py` | Parse `confidence` from LLM response, set on `SQLDraft` |
| `models/generation.py` | Add `confidence: float` field to `SQLDraft` |
| `entities/shared/column_filter.py` | **NEW** — `refine_columns()` pure function |
| `entities/nl2sql_controller/executor.py` | Call `refine_columns()` after dynamic SQL execution |
| `models/execution.py` | Add `hidden_columns`, `query_summary`, `query_confidence`, `error_suggestions` to `NL2SQLResponse` |
| `tests/unit/test_column_filter.py` | **NEW** — Tests for column stripping, capping, edge cases |

### P2 — Rich Metadata, Column Cap UI, Confidence Gate

| File | Change |
|------|--------|
| `entities/shared/tools/table_search.py` | Hydrate all `TableColumn` fields |
| `entities/query_builder/executor.py` | Pass full column metadata in `_build_generation_prompt()` |
| `entities/nl2sql_controller/executor.py` | Confidence gate logic before execution |
| `frontend/components/assistant-ui/nl2sql-tool-ui.tsx` | Hidden columns toggle, confirmation UI |
| `frontend/components/tool-ui/data-table/data-table.tsx` | Column visibility state |
| `tests/unit/test_confidence_gate.py` | **NEW** — Tests for confidence routing |

### P3 — Error Recovery

| File | Change |
|------|--------|
| `entities/nl2sql_controller/executor.py` | Classify failures, build recovery suggestions |
| `entities/orchestrator/orchestrator.py` | Allow suggestions on error responses |
| `tests/unit/test_error_recovery.py` | **NEW** — Tests for error classification and suggestions |

## Verification

```bash
# After each change
uv run poe check      # Lint, format, typecheck
uv run poe test       # Full test suite

# Frontend
cd src/frontend && pnpm dev  # Dev server for manual testing
```
