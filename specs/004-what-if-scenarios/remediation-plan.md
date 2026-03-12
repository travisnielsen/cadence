---
feature: 004-what-if-scenarios-review-remediation
created: 2026-03-11T00:00:00Z
author: Planner
status: ready-for-review
reviewers: []
---

# Design Document: Review Remediation for 004 What-If Scenarios

## 1. Overview

### Problem Statement

The code review of the `004-what-if-scenarios` branch identified 3 must-fix issues and 7 suggestions. The must-fix issues block production readiness: prompt hints are silently dropped at the SSE boundary, an unused parameter triggers linter warnings, and a broad exception handler risks information disclosure.

### Goals

- Fix all 3 must-fix issues from the review
- Address the most impactful suggestions (hint clickability, runtime payload validation)
- Pass `uv run poe check` cleanly after remediation

### Non-Goals (Out of Scope)

- Extracting `_SCENARIO_BASELINE_CONFIG` SQL to a config file (low impact, future work)
- Internationalization of `_extract_pct_from_query` (phase 2)
- Typing `ScenarioVisualizationPayload.rows` with a model (pragmatic dict is acceptable)
- `_infer_scenario_type` keyword improvements (needs phase-2 LLM extraction anyway)

## 2. Background

### Current State

All 50 implementation tasks are complete with 520+ tests passing. The review found 3 bugs/standards violations and 7 improvement suggestions. The 3 must-fix issues are:

1. **Prompt hints hardcoded to `[]`** — `chat.py` line 443 always emits `"prompt_hints": []` instead of serializing `result.scenario_hints`. US4 hints never reach the frontend.
2. **Unused `user_query` parameter** — `assistant.py` line 467 declares `user_query` in `build_scenario_assumption_set` but never uses it. Linter warning.
3. **Broad `except Exception`** — `pipeline.py` line 1422 catches all exceptions and returns `str(exc)` to the client, risking internal detail exposure.

Two suggestions are worth addressing now:

1. **Hint examples not clickable** — Frontend hint examples render with `cursor-pointer` but no `onClick` handler.
2. **Unsafe `as unknown as` cast** — `parseScenarioToolResult` in `chatApi.ts` has no runtime validation.

## 3. Design

### Decision

All fixes are small, localized, and low-risk. No architectural changes needed. Each fix maps to a single file edit with clear before/after.

## 4. Technical Design

### Affected Files

| File | Action | Purpose |
|------|--------|---------|
| `src/backend/api/routers/chat.py` | Modify | Fix #1: Serialize `result.scenario_hints` instead of `[]` |
| `src/backend/assistant/assistant.py` | Modify | Fix #2: Prefix unused param with `_` |
| `src/backend/nl2sql_controller/pipeline.py` | Modify | Fix #3: Narrow exception, sanitize error message |
| `src/frontend/components/assistant-ui/scenario-tool-ui.tsx` | Modify | Fix #4: Wire `onClick` on hint examples |
| `src/frontend/lib/chatApi.ts` | Modify | Fix #5: Add runtime field checks before cast |
| `tests/unit/test_process_query.py` | Modify | Add test for hints in SSE payload shape |

### Fix Details

#### Fix 1: Prompt hints emission (`chat.py`)

```python
# Before
"prompt_hints": [],

# After
"prompt_hints": [
    h.model_dump() for h in (result.scenario_hints or [])
],
```

The `result.scenario_hints` field is `list[PromptHint] | None`. The `or []` handles the `None` case. Each `PromptHint` is a Pydantic model, so `.model_dump()` produces a serializable dict.

#### Fix 2: Unused parameter (`assistant.py`)

```python
# Before
def build_scenario_assumption_set(
    self,
    scenario_intent: ScenarioIntent,
    user_query: str,
) -> ScenarioAssumptionSet:

# After
def build_scenario_assumption_set(
    self,
    scenario_intent: ScenarioIntent,
    _user_query: str,
) -> ScenarioAssumptionSet:
```

The `_` prefix signals intentional non-use. The docstring `Args:` entry stays as-is since the parameter still exists in the signature. Call sites pass the value unchanged (no API break).

The docstring `Args:` entry for `user_query` should also be updated to `_user_query`.

#### Fix 3: Narrow exception (`pipeline.py`)

```python
# Before
except Exception as exc:
    logger.exception("NL2SQL pipeline error")
    return NL2SQLResponse(sql_query="", error=str(exc))

# After
except (ValueError, RuntimeError, OSError, ValidationError) as exc:
    logger.exception("NL2SQL pipeline error")
    return NL2SQLResponse(
        sql_query="",
        error="An error occurred processing your query. Please try again.",
    )
```

`ValidationError` is already imported from `pydantic` at the top of the file. The generic message prevents internal detail leakage. The outer `chat.py` handler already has its own catch-all with `_sanitized_error_event()` as a safety net for truly unexpected errors.

#### Fix 4: Hint click handlers (`scenario-tool-ui.tsx`)

The `ClarificationHint` and `DiscoverabilityHint` components need an `onClick` handler on example list items. assistant-ui provides a `useComposerRuntime` hook to programmatically set input and submit. The approach:

- Import `useComposerRuntime` from `@assistant-ui/react`
- On click, set the composer text to the example prompt and optionally submit

**Design decision:** Set the text but do NOT auto-submit. This lets the user review/edit before sending, which is safer UX for scenario prompts that may need adjustment.

```tsx
// In ClarificationHint and DiscoverabilityHint:
const composerRuntime = useComposerRuntime();

<li
    onClick={() => composerRuntime.setText(ex)}
    // ...existing className...
>
```

Note: `useComposerRuntime` must be called within the assistant-ui component tree. Since `ScenarioToolUI` is rendered inside `makeAssistantToolUI`, which is within the `AssistantRuntimeProvider`, this hook is available.

If `useComposerRuntime` is not available in the project's version of assistant-ui, fall back to a simpler approach: copy the example text to clipboard with a toast notification.

#### Fix 5: Runtime payload validation (`chatApi.ts`)

```typescript
// Before
return r as unknown as ScenarioToolResult;

// After
if (
    !Array.isArray(r.metrics) ||
    typeof r.scenario_type !== "string"
) {
    return null;
}
return r as unknown as ScenarioToolResult;
```

This adds minimal runtime guards for the two required fields (`metrics` array and `scenario_type` string) without pulling in a validation library. Returns `null` for malformed payloads, which the UI already handles (renders nothing).

## 5. Implementation Plan

### Phase 1: Must-fix issues (backend)

- [x] [R001] Fix prompt hints emission in `chat.py` — serialize `result.scenario_hints`
- [x] [R002] Prefix `user_query` with `_` in `assistant.py` `build_scenario_assumption_set`
- [x] [R003] Narrow exception type and sanitize error message in `pipeline.py`

### Phase 2: Frontend improvements

- [x] [R004] Wire `onClick` on hint example prompts in `scenario-tool-ui.tsx`
- [x] [R005] Add runtime field checks in `parseScenarioToolResult` in `chatApi.ts`

### Phase 3: Test & verify

- [x] [R006] Add test asserting hints pass through SSE payload in `test_process_query.py`
- [x] [R007] Run `uv run poe check` — must pass with zero warnings
- [x] [R008] Verify linter warning for unused `user_query` is resolved

## 6. Testing Strategy

### Unit Tests

- **R006**: Add a test in `TestClarificationHints` that verifies `result.scenario_hints` is not empty and contains properly structured `PromptHint` objects with `kind`, `message`, `examples`, and `supported_types` fields. (The existing tests already assert hint presence on the `NL2SQLResponse`, but an explicit serialization test strengthens the contract.)

### Verification

- Run `uv run poe check` (lint + typecheck + metrics)
- Run `uv run poe test` to confirm no regressions
- Manually verify no linter warnings on the 3 modified backend files

## 7. Acceptance Criteria

- [x] `"prompt_hints"` in SSE scenario payload contains serialized hints (not `[]`)
- [x] No linter warnings on `assistant.py` for unused parameter
- [x] `pipeline.py` exception handler catches only specific types
- [x] Error message returned to client does not contain internal details
- [x] Hint example prompts are clickable in the frontend
- [x] `parseScenarioToolResult` rejects malformed payloads with `null`
- [x] `uv run poe check` passes cleanly
- [x] All existing tests continue to pass

## 8. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `useComposerRuntime` hook not available in current assistant-ui version | Low | Low | Fall back to clipboard copy approach |
| Narrowing exception types in pipeline misses an unexpected error class | Low | Med | Outer `chat.py` catch-all with `_sanitized_error_event()` serves as safety net |
| Adding runtime checks in `parseScenarioToolResult` causes false negatives | Low | Low | Only check 2 essential fields; `null` return is already handled gracefully |

## 9. Dependencies

- No new packages required
- `useComposerRuntime` from `@assistant-ui/react` (already a project dependency)
- `ValidationError` from `pydantic` (already imported in `pipeline.py`)

## 10. Open Questions

- [x] Should `build_scenario_assumption_set` keep the `user_query` param (prefixed) or remove it entirely? **Decision: Keep prefixed** — phase 2 will need it for assumption extraction from query text, and removing it would require re-adding later with a larger diff.
- [x] Should hint clicks auto-submit or just populate the input? **Decision: Populate only** — let users review/edit before sending.
