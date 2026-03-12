---
changes: specs/004-what-if-scenarios/tasks.md
created: 2025-06-29T00:00:00Z
agent: Reviewer
status: completed
ready_for_production: false
---

# Code Review: 004 What-If Scenarios

## Summary

**Ready for Production:** ❌ No (3 must-fix issues)
**Must Fix Issues:** 3
**Suggestions:** 7

## Must Fix ⛔

### 1. Prompt hints hardcoded to empty array in SSE emission

**File:** `src/backend/api/routers/chat.py` **Line:** 443
**Problem:** The scenario tool result SSE payload always emits `"prompt_hints": []` even though `result.scenario_hints` is available and populated by the pipeline.
**Impact:** Users never see clarification or discoverability hints in the frontend, defeating US4 (T035–T040). The entire hint subsystem is silently dropped at the boundary.
**Suggested Fix:**

```python
# Current (problematic)
"prompt_hints": [],

# Suggested (fixed)
"prompt_hints": [
    h.model_dump() for h in (result.scenario_hints or [])
],
```

### 2. Unused parameter `user_query` in `build_scenario_assumption_set`

**File:** `src/backend/assistant/assistant.py` **Line:** 467
**Problem:** The `user_query` parameter is declared but never used. The IDE/linter reports this as a compile warning. In a future phase the query text will be needed for full assumption extraction, but right now it's a dead parameter that violates the coding standard ("no placeholder code").
**Impact:** Linter warning. Misleads readers into thinking the query is being used for extraction.
**Suggested Fix:** Either prefix with `_` to signal intentional non-use, or remove the parameter and simplify the call sites until phase 2 needs it.

```python
# Option A: rename to signal intent
def build_scenario_assumption_set(
    self,
    scenario_intent: ScenarioIntent,
    _user_query: str,  # reserved for phase-2 extraction
) -> ScenarioAssumptionSet:
```

### 3. Broad `except Exception` in `process_query`

**File:** `src/backend/nl2sql_controller/pipeline.py` **Line:** 1422
**Problem:** Catches `Exception` broadly and returns `str(exc)` as the error message. This exposes internal error text (stack frame details, library messages) directly to the SSE response. The coding standard explicitly prohibits bare/broad excepts (BLE rule), and the linter flags this.
**Impact:** Potential information disclosure. Also masks unexpected errors like `TypeError` or `AttributeError` that should be caught during development. The `chat.py` outer handler already has its own `_sanitized_error_event()` which properly masks errors with a correlation ID.
**Suggested Fix:**

```python
# Current
except Exception as exc:
    logger.exception("NL2SQL pipeline error")
    return NL2SQLResponse(sql_query="", error=str(exc))

# Suggested: catch known recoverable errors only
except (ValueError, RuntimeError, OSError, ValidationError) as exc:
    logger.exception("NL2SQL pipeline error")
    return NL2SQLResponse(
        sql_query="",
        error="An error occurred processing your query. Please try again.",
    )
```

Note: the existing pre-feature code in `chat.py` line 537 has the same broad `except Exception` pattern — that's a pre-existing issue, not introduced by this feature.

## Nice to Have 💡

### 1. `_SCENARIO_BASELINE_CONFIG` SQL strings are not parameterized

**File:** `src/backend/nl2sql_controller/pipeline.py` **Lines:** 970–1010
**Suggestion:** The baseline SQL strings use only static queries with no user-supplied values, so there is no SQL injection risk. However, these hardcoded SQL strings are fragile and not easily testable against schema changes. Consider extracting them to a configuration file or constants module alongside `scenario_constants.py`.
**Benefit:** Easier to update when the database schema changes; more testable in isolation.

### 2. Type assertion in `parseScenarioToolResult` uses `as unknown as`

**File:** `src/frontend/lib/chatApi.ts` **Lines:** 40–44
**Suggestion:** The double cast `r as unknown as ScenarioToolResult` bypasses TypeScript's type system entirely. Consider using a runtime validation library (e.g., zod) or at minimum checking for the presence of required fields (`metrics`, `narrative`, `visualization`).
**Benefit:** Catches malformed payloads at the boundary instead of causing runtime crashes deeper in the component tree.

### 3. Missing React key stability concern in `ScenarioBarChart`

**File:** `src/frontend/components/assistant-ui/scenario-tool-ui.tsx` **Line:** ~175
**Suggestion:** The bar chart uses `xLabel` (a display string) as the React key. If two metrics have the same display label (e.g., aggregated by the same dimension), React will produce key collision warnings and potentially incorrect rendering.
**Benefit:** Use a combination of index and label, or use a unique metric identifier.

### 4. `_extract_pct_from_query` regex doesn't handle decimals with comma separator

**File:** `src/backend/nl2sql_controller/pipeline.py` **Lines:** 1065–1085
**Suggestion:** The regex `([+-]?\d+(?:\.\d+)?)\s*%` handles "5.5%" but not international formats like "5,5%" or ranges like "5-10%". This is fine for phase 1 but worth noting for internationalization.
**Benefit:** Future-proofing for non-English locales.

### 5. Hint example prompts are not clickable/actionable in the frontend

**File:** `src/frontend/components/assistant-ui/scenario-tool-ui.tsx` **Lines:** ~365, ~405
**Suggestion:** The hint examples render as list items with `cursor-pointer` and hover styles but have no `onClick` handler. Users see interactive-looking elements that don't do anything.
**Benefit:** Connect clicks to the chat input to actually submit the example prompt, completing the discoverability UX.

### 6. `ScenarioVisualizationPayload.rows` uses `dict` with `str | int | float | bool | None` values

**File:** `src/backend/models/scenario.py` **Lines:** 188–192
**Suggestion:** The `rows` field uses `list[dict[str, str | int | float | bool | None]]`. While this is acceptable for chart data, it's the only place in the scenario models that uses raw dicts instead of typed models. The coding standard prefers Pydantic models for I/O. Consider whether a typed row model would be beneficial.
**Benefit:** Type safety for chart row data. However, given that chart rows are inherently dynamic (keys depend on scenario type), the pragmatic choice of `dict` here is reasonable.

### 7. `_infer_scenario_type` keyword matching could produce false positives

**File:** `src/backend/assistant/assistant.py` **Lines:** 455–463
**Suggestion:** The keyword list `("demand", "volume", "order")` for demand scenarios would match "show me order details" (a data query), and `("inventory", "reorder", "stock")` would match "show stock levels" (also a data query). This is mitigated because `_infer_scenario_type` is only called after intent classification confirms scenario mode, but the keyword overlap with normal domain language is a reliability risk.
**Benefit:** More distinctive keywords or a secondary LLM classification step would improve type inference accuracy.

## Validation Checklist

- [x] Follows coding standards (Pydantic models, type hints, `Type | None`, Google docstrings)
- [x] No SQL injection risks (baseline queries are static; validated pipeline used for user queries)
- [x] No hardcoded secrets
- [ ] Prompt hints emitted to frontend (⛔ currently hardcoded to empty array)
- [x] Type hints on all params and returns
- [x] Google-style docstrings on public functions
- [x] Model validators enforce data consistency
- [x] Tests comprehensive (intent classification, pipeline shape, sparse signal, narrative, hints)
- [x] No `Optional[Type]` usage (uses `Type | None` correctly)
- [x] Import pattern follows `from models import X`
- [x] Async-first for I/O functions
- [x] Frontend TypeScript types mirror backend models accurately
- [x] Error handling present for unsupported types, SQL failures, empty baselines
- [ ] Linter warnings resolved (unused `user_query` param, broad exception)

## Positive Highlights 🌟

- **Excellent model design**: `ScenarioMetricValue` model validator enforces delta consistency at the Pydantic layer — impossible to create an inconsistent metric. Zero-baseline edge case is handled cleanly.
- **Deterministic computation**: All math is pure arithmetic with no LLM involvement, making results reproducible and testable. The `scenario_math.py` module is clean and well-documented.
- **Strong test coverage**: 20+ scenario-specific tests covering pipeline shape, sparse signal detection, narrative consistency, minimal-impact edge cases, and clarification hints. Tests verify actual numeric values, not just shapes.
- **Clean separation of concerns**: Constants, math, narrative, and hints each have their own module. The pipeline orchestrates without mixing concerns.
- **Frontend component architecture**: CSS-based bar chart avoids external dependencies. DataTable fallback ensures content is always visible even when visualization fails. Error boundary wrapping is correct.
- **Model validators as defensive contracts**: `ScenarioAssumptionSet._incomplete_when_missing` and `ScenarioIntent._scenario_requires_pattern` prevent invalid state at construction time.

## Next Steps

1. **Fix prompt hints emission** (Must-fix #1) — Change `"prompt_hints": []` to serialize `result.scenario_hints` in `chat.py`
2. **Address linter warnings** (Must-fix #2, #3) — Prefix unused param, narrow exception type
3. **Wire hint click handlers** (Nice-to-have #5) — Make example prompts clickable in the frontend
4. **Re-run quality gates** — `uv run poe check` after fixes to verify clean pass
