# Tasks: Assumption-Based What-If Scenarios

**Input**: Design documents from /specs/004-what-if-scenarios/
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish feature scaffolding and shared constants used by all stories.

- [x] T001 Create scenario constants module in src/backend/shared/scenario_constants.py
- [x] T002 [P] Add scenario feature labels and telemetry keys in src/backend/shared/scenario_constants.py
- [x] T003 [P] Add frontend scenario tool types scaffold in src/frontend/lib/scenario-types.ts

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define common contracts and plumbing required by all user stories.

**CRITICAL**: No user story implementation starts before this phase completes.

- [x] T004 Add ScenarioIntent, ScenarioAssumption, ScenarioAssumptionSet models in src/backend/models/scenario.py
- [x] T005 [P] Add ScenarioMetricValue, ScenarioComputationResult, ScenarioNarrativeSummary models in src/backend/models/scenario.py
- [x] T006 [P] Add ScenarioVisualizationPayload and PromptHint models in src/backend/models/scenario.py
- [x] T007 Export scenario models from src/backend/models/__init__.py
- [x] T008 Extend response contract fields for scenario payloads in src/backend/models/execution.py
- [x] T009 Add scenario math helper functions in src/backend/shared/scenario_math.py
- [x] T010 Add scenario hint builder functions in src/backend/shared/scenario_hints.py
- [x] T011 Add scenario tool result parsing support in src/frontend/lib/chatApi.ts

**Checkpoint**: Core contracts and shared utilities are available for all user stories.

---

## Phase 3: User Story 1 - Detect and Route What-If Requests (Priority: P1) 🎯 MVP

**Goal**: Recognize flexible what-if prompts and route to scenario processing while preserving current non-scenario behavior.

**Independent Test**: Submit mixed scenario and non-scenario prompts and verify routing classification and fallback behavior.

### Tests for User Story 1

- [x] T012 [P] [US1] Add scenario intent classification tests in tests/unit/test_data_assistant.py
- [x] T013 [P] [US1] Add non-scenario regression routing tests in tests/unit/test_data_assistant.py

### Implementation for User Story 1

- [x] T014 [US1] Add what-if routing rules and confidence handling in src/backend/assistant/assistant_prompt.md
- [x] T015 [US1] Implement scenario intent branch in src/backend/assistant/assistant.py
- [x] T016 [US1] Integrate scenario request object creation in src/backend/assistant/assistant.py
- [x] T017 [US1] Add scenario routing observability fields in src/backend/api/routers/chat.py
- [x] T018 [US1] Add scenario branch entry point in src/backend/nl2sql_controller/pipeline.py

**Checkpoint**: What-if prompts are reliably routed to scenario workflow.

---

## Phase 4: User Story 2 - View Interactive Scenario Results (Priority: P1)

**Goal**: Return baseline vs scenario payloads and render interactive charts with native assistant-ui/tool-ui primitives.

**Independent Test**: Execute a scenario request and verify chart-capable tool result is rendered in-thread with baseline/scenario comparisons.

### Tests for User Story 2

- [x] T019 [P] [US2] Add scenario payload shape tests for pipeline output in tests/unit/test_process_query.py
- [x] T020 [P] [US2] Add scenario stream tool-call contract tests in tests/integration/test_workflow_integration.py
- [x] T046 [P] [US2] [SC-009] Add sparse-signal and missing-signal handling tests in tests/unit/test_process_query.py

### Implementation for User Story 2

- [x] T021 [US2] Implement baseline aggregation query helpers in src/backend/shared/scenario_math.py
- [x] T022 [US2] Implement assumption transform calculations in src/backend/shared/scenario_math.py
- [x] T023 [US2] Build ScenarioComputationResult assembly in src/backend/nl2sql_controller/pipeline.py
- [x] T024 [US2] Build ScenarioVisualizationPayload mapping in src/backend/nl2sql_controller/pipeline.py
- [x] T025 [US2] Emit scenario_analysis tool result payload in src/backend/api/routers/chat.py
- [x] T026 [US2] Create scenario tool UI component using native primitives in src/frontend/components/assistant-ui/scenario-tool-ui.tsx
- [x] T027 [US2] Register scenario tool UI with assistant runtime in src/frontend/app/assistant.tsx
- [x] T028 [US2] Add chart-capable renderer wiring using tool-ui components in src/frontend/components/assistant-ui/scenario-tool-ui.tsx
- [x] T029 [US2] Add fallback numeric table rendering for chart failures in src/frontend/components/assistant-ui/scenario-tool-ui.tsx
- [x] T047 [US2] [SC-009] Implement sparse-signal detection and insufficiency messaging in src/backend/nl2sql_controller/pipeline.py

**Checkpoint**: Scenario responses show interactive chart content with fallback table behavior.

---

## Phase 5: User Story 3 - Receive Brief Narrative Impact Summary (Priority: P2)

**Goal**: Include concise, numerically consistent impact analysis with scenario responses.

**Independent Test**: Run scenario requests with high and low deltas and verify summary text aligns with computed results.

### Tests for User Story 3

- [x] T030 [P] [US3] Add narrative consistency tests against computed deltas in tests/unit/test_process_query.py

### Implementation for User Story 3

- [x] T031 [US3] Implement deterministic narrative summary builder in src/backend/shared/scenario_narrative.py
- [x] T032 [US3] Integrate narrative summary generation in src/backend/nl2sql_controller/pipeline.py
- [x] T033 [US3] Render narrative summary block in scenario tool UI in src/frontend/components/assistant-ui/scenario-tool-ui.tsx
- [x] T034 [US3] Add minimal-impact narrative handling in src/backend/shared/scenario_narrative.py

**Checkpoint**: Every successful scenario response includes concise narrative analysis consistent with returned metrics.

---

## Phase 6: User Story 4 - Use Prompt Hints to Improve Scenario Inputs (Priority: P3)

**Goal**: Provide hints for both clarification and discoverability of supported scenario types.

**Independent Test**: Submit ambiguous and discoverability prompts and verify returned hints include missing inputs and supported scenario examples.

### Tests for User Story 4

- [x] T035 [P] [US4] Add clarification hint tests for missing assumptions in tests/unit/test_process_query.py
- [x] T036 [P] [US4] Add discoverability hint tests for supported scenario categories in tests/unit/test_process_query.py

### Implementation for User Story 4

- [x] T037 [US4] Implement clarification hint generation in src/backend/shared/scenario_hints.py
- [x] T038 [US4] Implement discoverability hint generation in src/backend/shared/scenario_hints.py
- [x] T039 [US4] Integrate prompt hint emission in scenario flow (clarification hints) in src/backend/nl2sql_controller/pipeline.py
- [x] T040 [US4] Render prompt hints and examples in scenario tool UI in src/frontend/components/assistant-ui/scenario-tool-ui.tsx

**Checkpoint**: Hint behavior supports both missing-input clarification and scenario-type discovery.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, docs alignment, and quality hardening across all stories.

- [x] T041 [P] Update scenario response schema examples in specs/004-what-if-scenarios/contracts/scenario-response.schema.json
- [x] T042 [P] Update OpenAPI contract examples in specs/004-what-if-scenarios/contracts/scenario-api.openapi.yaml
- [x] T043 Validate quickstart flow and command accuracy in specs/004-what-if-scenarios/quickstart.md
- [x] T044 Run backend quality gate and fix issues in src/backend/
- [x] T045 Run frontend lint/build validation and fix issues in src/frontend/
- [x] T048 [P] Add scenario latency benchmark test coverage for SC-006 in tests/integration/test_workflow_integration.py
- [x] T049 Add scenario latency comparison validation workflow in specs/004-what-if-scenarios/quickstart.md
- [x] T050 [P] Add prompt-hint usability validation script for SC-007 in specs/004-what-if-scenarios/quickstart.md

---

## Phase 8: Post-Implementation Remediation

**Purpose**: Address runtime issues, code review findings, and design refinements discovered after initial implementation.

### Bug Fixes

- [x] R001 Fix circular import crash: replace cross-package import in src/backend/models/scenario.py with private module-level constants
- [x] R002 Fix Azure Monitor duration error: suppress noisy exporter log in src/backend/api/monitoring.py

### Design Refinement: LLM-Driven Scenario Discovery (replaces regex-based detection)

- [x] R003 Add `scenario_discovery: bool` field to ClassificationResult in src/backend/assistant/assistant.py
- [x] R004 Update LLM classification prompt to emit `scenario_discovery: true` for capability inquiries in src/backend/assistant/assistant.py
- [x] R005 Update assistant system prompt with scenario discovery guidance in src/backend/assistant/assistant_prompt.md
- [x] R006 Replace regex-based `is_discovery_prompt()` with `classification.scenario_discovery` flag in src/backend/api/routers/chat.py
- [x] R007 Remove `_DISCOVERY_PATTERNS` regex, `is_discovery_prompt()`, and discovery-only early return from src/backend/nl2sql_controller/pipeline.py
- [x] R008 Update discovery hint tests to reflect LLM-driven architecture in tests/unit/test_process_query.py

**Checkpoint**: Discovery prompts are handled via LLM natural language understanding, not regex patterns.

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): Starts immediately.
- Foundational (Phase 2): Depends on Setup and blocks all user stories.
- User Stories (Phases 3-6): Depend on Foundational completion.
- Polish (Phase 7): Depends on completion of desired user stories.

### User Story Dependencies

- US1 (P1): Starts after Phase 2; no dependency on other user stories.
- US2 (P1): Starts after Phase 2; depends on shared contracts and can proceed in parallel with US1 once foundation is ready.
- US3 (P2): Depends on US2 scenario computation outputs.
- US4 (P3): Depends on US1 routing and shared scenario models; can run in parallel with late US2/US3 work once scenario branch exists.

### Within Each User Story

- Implement tests first for each story-specific behavior.
- Backend response generation before frontend rendering for that story.
- Story checkpoint must pass before moving to lower-priority stories.

### Parallel Opportunities

- T002 and T003 can run in parallel with T001.
- T005, T006, and T011 can run in parallel after T004 starts.
- US1 tests (T012, T013) can run in parallel.
- US2 tests (T019, T020) can run in parallel.
- US4 tests (T035, T036) can run in parallel.
- Contract polish tasks T041 and T042 can run in parallel.

---

## Parallel Example: User Story 2

```bash
# Run in parallel after foundational phase
Task T019: Add scenario payload shape tests in tests/unit/test_process_query.py
Task T020: Add scenario stream tool-call contract tests in tests/integration/test_workflow_integration.py

# Parallel UI/backend split
Task T023: Build ScenarioComputationResult assembly in src/backend/nl2sql_controller/pipeline.py
Task T026: Create scenario tool UI component in src/frontend/components/assistant-ui/scenario-tool-ui.tsx
```

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Complete Setup and Foundational phases.
2. Deliver US1 routing so scenario intent is detected safely.
3. Deliver US2 payload + native chart rendering for end-user value.
4. Validate with scenario prompt walkthrough before adding narrative/hints.

### Incremental Delivery

1. US1: Intent and routing correctness.
2. US2: Interactive baseline vs scenario chart responses.
3. US3: Concise narrative impact explanation.
4. US4: Prompt hint clarification and discoverability.
5. Polish: Contract and quality gates.

### Team Parallelization

1. Backend engineer: US1 and backend portions of US2.
2. Frontend engineer: US2 rendering plus US4 hint UI.
3. QA/validation: Story tests and integration checks as stories complete.

---

## Notes

- [P] tasks indicate parallel-safe work across different files.
- Story labels map each task to a specific user story for traceability.
- Keep scenario charts on native assistant-ui/tool-ui primitives to satisfy FR-016 and SC-008.
- Use uv run poe check as the backend quality gate before completing implementation.
