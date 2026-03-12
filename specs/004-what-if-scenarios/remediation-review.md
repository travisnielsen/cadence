---
changes: specs/004-what-if-scenarios/remediation-plan.md
created: 2025-07-12T00:00:00Z
agent: Reviewer
status: completed
ready_for_production: true
---

# Code Review: Remediation for 004 What-If Scenarios

## Summary

**Ready for Production:** ✅ Yes
**Must Fix Issues:** 0
**Suggestions:** 2

All 8 remediation tasks (R001–R008) are correctly implemented, pass quality gates, and meet the acceptance criteria defined in the remediation plan. The three must-fix issues from the original review (prompt hints dropped, unused parameter, broad exception) are fully resolved. The two additional improvements (hint clickability, runtime payload validation) are solid.

## Must Fix ⛔

None. All remediation changes are correct and standards-compliant.

## Nice to Have 💡

### 1. Clickable `<li>` elements lack keyboard accessibility

**File:** `src/frontend/components/assistant-ui/scenario-tool-ui.tsx` **Lines:** 324, 360
**Suggestion:** The `ClarificationHint` and `DiscoverabilityHint` components attach `onClick` to `<li>` elements but lack `role="button"`, `tabIndex={0}`, and an `onKeyDown` handler for Enter/Space. The existing `SuggestionPills` in `nl2sql-tool-ui.tsx` (line 253) correctly uses semantic `<button>` elements. Either switch to `<button>` or add the ARIA attributes.
**Benefit:** Keyboard and screen reader users can activate hint examples.

```tsx
// Current
<li
    key={ex}
    onClick={() => handleExampleClick(ex)}
    className="cursor-pointer ..."
>

// Suggested (option A — semantic button)
<li key={ex}>
    <button
        type="button"
        onClick={() => handleExampleClick(ex)}
        className="w-full text-left cursor-pointer ..."
    >
        {ex}
    </button>
</li>

// Suggested (option B — ARIA on li)
<li
    key={ex}
    role="button"
    tabIndex={0}
    onClick={() => handleExampleClick(ex)}
    onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") handleExampleClick(ex);
    }}
    className="cursor-pointer ..."
>
```

### 2. Duplicate `handleExampleClick` callback across two components

**File:** `src/frontend/components/assistant-ui/scenario-tool-ui.tsx` **Lines:** 310–315 and 347–352
**Suggestion:** `ClarificationHint` and `DiscoverabilityHint` define identical `useThreadRuntime` + `useCallback` blocks. This could be extracted into a shared hook (`useSetComposerText`) to reduce duplication.
**Benefit:** DRY — single place to change if behavior evolves (e.g., adding auto-submit later).

```tsx
function useSetComposerText() {
    const threadRuntime = useThreadRuntime();
    return useCallback(
        (text: string) => threadRuntime.composer.setText(text),
        [threadRuntime],
    );
}
```

## Validation Against Remediation Plan

| Task | Spec | Verdict |
|------|------|---------|
| R001 — Prompt hints emission | `model_dump()` with `or []` null safety | ✅ Correct |
| R002 — Unused parameter | `_user_query` prefix, docstring updated | ✅ Correct |
| R003 — Narrow exception | 4 specific types, sanitized message, `ValidationError` already imported | ✅ Correct |
| R004 — Hint click handlers | `useThreadRuntime` + `useCallback`, setText only (design decision) | ✅ Correct |
| R005 — Runtime payload validation | 2-field guard (`metrics` array, `scenario_type` string), null return | ✅ Correct |
| R006 — Serialization test | Verifies `model_dump()` output structure with correct field types | ✅ Correct |
| R007 — Quality gates | `uv run poe check` passes, 521 tests | ✅ Confirmed |
| R008 — Linter clean | No warnings on modified files | ✅ Confirmed |

## Standards Compliance

- [x] Line length ≤ 100 characters
- [x] Type hints on all parameters and returns
- [x] Google-style docstrings (updated for `_user_query`)
- [x] Uses `Type | None` not `Optional[Type]`
- [x] Async by default where appropriate
- [x] No bare excepts (narrowed to specific types)
- [x] Pydantic `model_dump()` for serialization (not raw dicts)
- [x] Error messages sanitized (no internal detail leakage)
- [x] Security: outer `chat.py` catch-all at line 537 with `_sanitized_error_event()` confirmed as safety net

## Positive Highlights 🌟

- **R001 fix is clean and idiomatic** — `[h.model_dump() for h in (result.scenario_hints or [])]` matches the adjacent `assumptions` serialization pattern exactly
- **R003 safety analysis is thorough** — narrowed exceptions cover all realistic failure modes in the pipeline (search errors → `OSError`/`RuntimeError`, validation → `ValueError`/`ValidationError`), and the outer catch-all in `chat.py` guards against anything unexpected
- **R004 follows established codebase pattern** — `useThreadRuntime` + `composer.setText()` mirrors `nl2sql-tool-ui.tsx` exactly
- **R005 is minimal and effective** — guards only the two essential fields without over-engineering with a validation library
- **R006 test is well-structured** — validates the full `model_dump()` contract including field types, not just presence
- **Updated `test_unexpected_exception_returns_error`** correctly asserts the new sanitized message

## Next Steps

The two suggestions above are low-priority improvements that can be addressed in a follow-up. The code is production-ready as-is.
