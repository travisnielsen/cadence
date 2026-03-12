# Implementation Plan: Assumption-Based What-If Scenarios

**Branch**: `004-what-if-scenarios` | **Date**: 2026-03-11 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-what-if-scenarios/spec.md`

## Summary

Add phase-1 assumption-based what-if support to the existing chat experience by extending intent routing, adding scenario computation outputs, rendering interactive chart responses using native assistant-ui/tool-ui patterns, and returning concise narrative analysis plus discoverable prompt hints.

Implementation is split into four slices aligned to the spec priorities:

- **P1**: What-if intent detection + scenario routing.
- **P1**: Baseline vs scenario data payloads + interactive chart rendering path.
- **P2**: Concise narrative impact analysis.
- **P3**: Prompt hints for both clarification and scenario-type discoverability.

## Technical Context

**Language/Version**: Python 3.11+ (backend), TypeScript/React 19 + Next.js 16 (frontend)
**Primary Dependencies**: FastAPI, Microsoft Agent Framework (`agent-framework`), Pydantic v2, `@assistant-ui/react`, existing tool-ui components
**Storage**: Azure SQL (WideWorldImportersStd), Azure AI Search metadata indexes
**Testing**: `pytest`/`pytest-asyncio`, `uv run poe check`, frontend linting via Next.js
**Target Platform**: Linux-hosted backend API + browser-based frontend chat UI
**Project Type**: Web application (backend + frontend monorepo)
**Performance Goals**: Scenario responses remain within 20% median latency of comparable analytical responses (SC-006)
**Constraints**: Read-only SQL execution, historical-date anchoring rules, no custom chart pipeline for phase 1, phase-1 arithmetic scenarios only (no predictive modeling)
**Scale/Scope**: One feature slice spanning routing, response contract, backend computation formatting, and frontend rendering in existing assistant thread UX

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Async-First | **PASS** | Existing async pipeline and SSE path remain; scenario processing extends current async request flow without introducing blocking I/O. |
| II. Validated Data at Boundaries | **PASS** | New scenario response payloads and hint metadata will be modeled with Pydantic objects before crossing API/tool boundaries. |
| III. Fully Typed | **PASS** | New/updated backend functions and models will include full parameter and return typing. |
| IV. Single-Responsibility Executors | **PASS** | No new executor role needed; logic remains distributed by concern (assistant intent routing, pipeline processing, UI rendering). |
| V. Automated Quality Gates | **PASS** | Plan and tasks include `uv run poe check` and targeted tests for routing, payload shape, and UI rendering. |

**Post-Phase 1 Re-check**: PASS. Design artifacts preserve async flow, typed boundaries, Pydantic contracts, and existing responsibility boundaries.

## Project Structure

### Documentation (this feature)

```text
specs/004-what-if-scenarios/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── scenario-response.schema.json
│   └── scenario-api.openapi.yaml
└── tasks.md              # Produced by /speckit.tasks
```

### Source Code (repository root)

```text
src/backend/
├── assistant/
│   ├── assistant.py                    # Intent detection/routing updates
│   └── assistant_prompt.md             # What-if detection guidance updates
├── api/routers/
│   └── chat.py                         # Streaming payload support for scenario chart/tool results
├── models/
│   ├── execution.py                    # Scenario response contract fields
│   └── (new/updated scenario models)   # Scenario assumptions, chart payload, hints
├── nl2sql_controller/
│   └── pipeline.py                     # Scenario branch integration and orchestration
└── shared/
    └── (utility modules)               # Scenario math helpers / hint assembly as needed

src/frontend/
├── components/assistant-ui/
│   ├── thread.tsx                      # Register scenario tool result renderer
│   └── scenario-tool-ui.tsx            # Native assistant-ui/tool-ui chart composition
└── components/tool-ui/
    └── (chart-related wrappers)        # Reuse native tool-ui chart primitives

tests/
├── unit/
│   ├── test_data_assistant.py          # What-if intent routing behavior
│   ├── test_process_query.py           # Scenario pipeline outputs
│   └── (new scenario tests)            # Assumption handling, hints, narrative consistency
└── integration/
    └── test_workflow_integration.py    # End-to-end scenario response flow
```

**Structure Decision**: Use the existing web-app structure and extend current backend chat/pipeline and frontend assistant-ui tool rendering paths. No new application layer is introduced.

## Phase Plan

### Phase 0: Research and Decision Lock

- Confirm intent-detection strategy for flexible what-if phrasing.
- Confirm phase-1 assumption model scope (price/demand/cost/reorder categories).
- Confirm chart rendering approach constrained to native assistant-ui/tool-ui primitives.
- Confirm narrative generation strategy and guardrails for consistency with computed values.
- Confirm prompt-hint behavior for both clarification and discoverability.

Output: `research.md` with all decisions and alternatives.

### Phase 1: Design and Contracts

- Define scenario entities and state flow in `data-model.md`.
- Define machine-readable scenario response contract and API interaction contract under `contracts/`.
- Create `quickstart.md` with implementation sequence and validation steps.
- Update agent context via `.specify/scripts/bash/update-agent-context.sh copilot`.

Output: `data-model.md`, `contracts/*`, `quickstart.md`, and updated agent context.

### Phase 2: Task Planning (completed)

- Generate dependency-ordered tasks with `/speckit.tasks` after design sign-off.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Intent over-triggering on non-scenario prompts | Incorrect routing and user confusion | Add explicit non-scenario negative examples in routing prompt/tests; enforce fallback to standard NL2SQL path. |
| Chart payload drift from frontend expectations | Broken or degraded visualization rendering | Lock schema in `contracts/scenario-response.schema.json`; add integration tests for payload shape. |
| Narrative text contradicts numeric outputs | Trust erosion | Build narrative from computed deltas, not free-form generation only; add consistency assertions in tests. |
| Users unsure which scenarios are supported | Reduced adoption | Enforce FR-015 with LLM-driven scenario discovery (via `scenario_discovery` flag on conversation classification) that emits discoverability hint cards. |
| Framework mismatch (custom chart path introduced) | Scope creep and maintenance burden | Enforce FR-016/SC-008 by constraining UI implementation to native assistant-ui/tool-ui chart capabilities. |

## Complexity Tracking

No constitution violations identified; no complexity exceptions required.
