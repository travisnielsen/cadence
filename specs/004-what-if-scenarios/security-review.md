---
changes: specs/004-what-if-scenarios/tasks.md
created: 2026-03-11T00:00:00Z
agent: Security
status: completed
risk_level: low
ready_for_production: true
---

# Security Review: 004 What-If Scenarios

## Summary

**Risk Level:** 🟢 Low
**Ready for Production:** ✅ Yes (with advisories noted)
**Critical Issues:** 0
**Warnings:** 3

The what-if scenario feature has a solid security posture. SQL queries are hardcoded server-side constants (no user-derived SQL), error messages are sanitized, Pydantic validates all I/O boundaries, and the frontend uses React's safe rendering (no `dangerouslySetInnerHTML`). The LLM interaction uses the existing trusted classification pattern with confidence gating. No critical or high-severity findings identified.

## Critical Findings ⛔

None.

## Warnings ⚠️

### 1. Unsanitized `scenario_type` in Error Response

**Severity:** Low
**OWASP Reference:** A03 — Injection / Information Disclosure
**File:** `src/backend/nl2sql_controller/pipeline.py` **Line:** 1243
**Description:** When `_SCENARIO_BASELINE_CONFIG` doesn't contain a matching key, the error message interpolates `assumption_set.scenario_type` directly:

```python
error=(f"Unsupported scenario type: {assumption_set.scenario_type}")
```

While `scenario_type` is currently always set from `_infer_scenario_type()` which only returns hardcoded constants (`SCENARIO_TYPE_PRICE`, `SCENARIO_TYPE_DEMAND`, etc.), the `ScenarioAssumptionSet.scenario_type` field is typed as `str` — not a `Literal`. If a future code path allows the LLM or user input to influence this field, the error message would echo arbitrary content to the client.

**Current Risk:** Negligible — `_infer_scenario_type()` always returns from a fixed set of 4 constants, and this value never originates from raw user input.

**Recommendation:** Constrain `ScenarioAssumptionSet.scenario_type` to `Literal` of supported types, or sanitize the error message:

```python
# Option A: Constrain at the model level
scenario_type: Literal["price_delta", "demand_delta", "supplier_cost_delta", "inventory_policy_delta"]

# Option B: Generic error message
error="Unsupported scenario type"
```

### 2. No Input Length Constraint on Chat Message Query Parameter

**Severity:** Low
**OWASP Reference:** A04 — Insecure Design (resource exhaustion)
**File:** `src/backend/api/routers/chat.py` **Line:** 553
**Description:** The `message` query parameter has no `max_length` constraint:

```python
message: str = Query(..., description="User message"),
```

An attacker could send an extremely long message to consume LLM tokens, increase processing time, and potentially cause memory pressure. This is a pre-existing issue not specific to the scenario feature, but the scenario pathway exercises the same input.

**Current Risk:** Low — Azure AD auth middleware requires a valid JWT, limiting abuse to authenticated users. The LLM API has its own token limits.

**Recommendation:** Add `max_length` to the `Query` parameter:

```python
message: str = Query(..., max_length=4000, description="User message"),
```

### 3. LLM-Controlled `detected_patterns` Flows Into Hint Logic

**Severity:** Low
**OWASP Reference:** LLM01 — Prompt Injection
**File:** `src/backend/assistant/assistant.py` **Lines:** 345-355
**File:** `src/backend/nl2sql_controller/pipeline.py` **Lines:** 1186-1188
**Description:** The `detected_patterns` list in `ScenarioIntent` is populated from the LLM classification response:

```python
detected = parsed.get("detected_patterns", [])
```

This is then passed to `_build_scenario_hints()` → `_is_discovery_prompt()` where it's iterated for substring matching:

```python
return any("option" in p.lower() or "discover" in p.lower() for p in detected_patterns)
```

If the LLM is manipulated through prompt injection to return crafted `detected_patterns`, it could influence which hints are shown. However, the consequences are limited — the worst outcome is showing a discoverability hint when it shouldn't appear, or suppressing one.

**Current Risk:** Negligible — the `detected_patterns` values only influence hint selection (clarification vs discoverability), not SQL execution, data access, or security-sensitive operations. The `_infer_scenario_type()` method also uses these patterns but only for keyword matching against a fixed set of supported types with a safe default (`SCENARIO_TYPE_PRICE`).

**Recommendation:** No code change required. This is an inherent property of LLM-based classification. The defense-in-depth approach is sound: patterns only influence cosmetic hint selection, not data operations.

## Security Checklist

### Authentication & Authorization

- [x] Chat endpoint protected by Azure AD JWT middleware (`AzureADAuthMiddleware`)
- [x] `get_optional_user_id` extracts authenticated user from request state
- [x] No new endpoints introduced — scenario processing reuses the existing `/api/chat/stream` endpoint
- [x] No new public paths added to `PUBLIC_PATHS`

### Data Protection

- [x] No PII in scenario models — only metric names, dimension keys, and numeric values
- [x] Error messages sanitized — inner `pipeline.py` catch returns generic message, outer `chat.py` catch uses `_sanitized_error_event()` with correlation ID
- [x] Scenario computations use server-side aggregated data only — no user data is stored or returned beyond what the baseline query provides
- [x] No secrets or credentials in scenario-related code
- [x] Logging uses structured format with `%.80s` truncation for user queries in `process_scenario_query`

### Input Validation

- [x] `ScenarioIntent` validates with Pydantic: `mode` is `Literal`, `confidence` has `ge=0.0, le=1.0` constraints, `detected_patterns` requires at least one entry for `mode="scenario"`
- [x] `ScenarioAssumption` validates with Pydantic: `unit` and `source` are `Literal` types
- [x] `ScenarioAssumptionSet` has model validator enforcing `is_complete=False` when `missing_requirements` is non-empty
- [x] `ScenarioMetricValue` has model validator ensuring delta consistency (delta_abs = scenario - baseline)
- [x] `ScenarioVisualizationPayload` constrains `chart_type` to `Literal["bar", "line", "combo"]` and requires `min_length=2` for series
- [x] `_extract_pct_from_query` regex patterns are safe — no catastrophic backtracking risk (simple alternation with `\d+(?:\.\d+)?` and `\s*`)
- [x] Assumption value ranges validated via `DEFAULT_ASSUMPTION_RANGES` (though clamping not yet applied — see note below)
- [x] Frontend `parseScenarioToolResult` includes runtime guards (`Array.isArray(r.metrics)`, `typeof r.scenario_type`)

### SQL Injection Prevention

- [x] **No user-derived SQL** — baseline queries are hardcoded constants in `_SCENARIO_BASELINE_CONFIG`
- [x] `user_query` is never interpolated into SQL strings in the scenario pathway
- [x] Scenario computation is purely arithmetic (apply percentage/absolute to aggregated baseline values)
- [x] The existing `validate_query` / query allowlist infrastructure is not bypassed — scenario queries are pre-defined

### XSS Prevention

- [x] Frontend uses React's safe rendering — no `dangerouslySetInnerHTML` in `scenario-tool-ui.tsx`
- [x] All data rendered via JSX text interpolation (`{hint.message}`, `{ex}`, etc.)
- [x] No HTML parsing of server responses

### AI/LLM Specific

- [x] Prompt injection mitigated — LLM output only influences intent classification, not SQL construction
- [x] Confidence threshold gating (`SCENARIO_ROUTING_CONFIDENCE_THRESHOLD = 0.6`) prevents low-confidence classifications from triggering scenario processing
- [x] LLM-controlled `detected_patterns` validated: only `list` type accepted, non-list values rejected
- [x] Narrative text is deterministic (derived from computed values, not LLM-generated) — FR-007 compliance
- [x] No LLM-generated code is executed
- [x] Scenario type inference uses keyword matching with safe default, not direct LLM output passthrough

### Error Handling & Information Disclosure

- [x] Pipeline `except (ValueError, RuntimeError, OSError, ValidationError)` with sanitized generic error message
- [x] Outer `chat.py` catch-all with `_sanitized_error_event()` returns generic error + correlation ID (line 537)
- [x] No stack traces exposed to client
- [x] No internal path or configuration details leaked in error responses

### Resource & DoS Considerations

- [x] Baseline queries use `GROUP BY` aggregation — bounded result set, not full table scans
- [x] Sparse-signal detection (`MIN_BASELINE_ROWS=2`, `MIN_DISTINCT_WEEKLY_PERIODS=8`) provides early exit for insufficient data
- [x] No unbounded loops in scenario computation — iterates over aggregated dimension keys only
- [x] Frontend `useMemo` prevents re-computation on re-render

## Positive Security Practices 🛡️

- **Hardcoded SQL Only**: The `_SCENARIO_BASELINE_CONFIG` pattern completely eliminates SQL injection risk for the scenario pathway. User input never touches SQL.
- **Deterministic Narrative**: Narrative summaries are built from computed values only (no LLM generation), preventing hallucinated or manipulated text in results.
- **Defense in Depth**: Three-layer error handling — specific exceptions in pipeline, outer catch-all in chat.py, and `_sanitized_error_event()` with correlation IDs for debugging without information leakage.
- **Pydantic Validation Throughout**: Every data boundary uses Pydantic models with constrained types (`Literal`, `ge`/`le`, `min_length`/`max_length`), model validators for cross-field consistency, and `model_dump()` for serialization.
- **Frontend Runtime Guards**: `parseScenarioToolResult` validates payload shape before accepting data from the SSE stream, preventing malformed payloads from causing runtime errors.
- **Confidence Gating**: LLM classification must exceed a 0.6 confidence threshold and have at least one detected pattern before scenario routing activates — this limits over-triggering from adversarial inputs.

## Advisory Notes (Non-Blocking)

### Assumption Range Enforcement

`DEFAULT_ASSUMPTION_RANGES` is defined in `scenario_constants.py` (e.g., price_delta: -50% to +100%) but the current phase-1 code does not actively clamp or reject values outside these ranges. The `_extract_pct_from_query` function can extract any numeric value from user input. For phase 2, consider adding validation that rejects or clamps extracted values against these ranges, with a clear user-facing message.

### No Rate Limiting

The chat endpoint has no rate limiting beyond Azure AD authentication. This is a pre-existing condition not introduced by this feature. Consider adding rate limiting per user for production deployment.

## Next Steps

1. Consider constraining `ScenarioAssumptionSet.scenario_type` to a `Literal` type (Warning #1)
2. Consider adding `max_length` to the chat message query parameter (Warning #2)
3. In phase 2, enforce `DEFAULT_ASSUMPTION_RANGES` validation on extracted percentage values
4. No findings block deployment — the feature is production-ready from a security perspective
