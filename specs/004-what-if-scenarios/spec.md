# Feature Specification: Assumption-Based What-If Scenarios

**Feature Branch**: `004-what-if-scenarios`
**Created**: 2026-03-11
**Status**: Draft
**Input**: User description: "Create a new specification for assumption-based what-if scenarios with flexible what-if intent detection, interactive scenario charts, brief explanatory analysis, and prompt hints"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Detect and Route What-If Requests (Priority: P1)

As an analyst using chat, I can ask open-ended scenario questions (for example, "what if we raise prices 5%" or "if supplier costs increase, what changes?") and the system recognizes this as a what-if request and routes it to scenario processing instead of generic Q&A.

**Why this priority**: Without reliable detection and routing, no scenario capability is reachable.

**Independent Test**: Submit a set of what-if and non-what-if prompts and verify the system correctly classifies and routes each request.

**Acceptance Scenarios**:

1. **Given** a user asks a direct what-if prompt, **When** the orchestrator evaluates intent, **Then** the request is classified as a scenario request and sent to the scenario workflow.
2. **Given** a user asks a conversational prompt without scenario intent, **When** the orchestrator evaluates intent, **Then** the request remains in normal conversational or standard analytics routing.
3. **Given** a user asks a novel phrasing of scenario intent, **When** the orchestrator evaluates intent, **Then** the system still identifies it as what-if intent without requiring exact keywords.

---

### User Story 2 - View Interactive Scenario Results (Priority: P1)

As an analyst, I can see baseline and scenario outcomes in interactive charts so I can quickly understand direction and magnitude of change.

**Why this priority**: Visual comparison is the core user-facing value for scenario exploration.

**Independent Test**: Execute one what-if request and verify the response includes a usable interactive chart showing baseline versus scenario values.

**Acceptance Scenarios**:

1. **Given** a scenario request is processed, **When** the response is returned, **Then** at least one interactive chart is included comparing baseline and scenario outputs.
2. **Given** a chart is rendered, **When** the user interacts with it, **Then** the chart reveals values needed to interpret changes (for example, category-level or item-level comparisons).
3. **Given** multiple metrics are part of the scenario, **When** results are returned, **Then** the chart set clearly distinguishes each metric and keeps baseline/scenario labeling unambiguous.

---

### User Story 3 - Receive Brief Narrative Impact Summary (Priority: P2)

As an analyst, I receive a concise written analysis that explains what changed and why it matters, so I can interpret results without manually deriving conclusions.

**Why this priority**: Users need quick interpretation, but this is secondary to correct detection and charted output.

**Independent Test**: Run a scenario and verify that a short narrative summary appears and matches charted and tabular values.

**Acceptance Scenarios**:

1. **Given** a scenario result is available, **When** the response is assembled, **Then** a short explanatory summary highlights major increases, decreases, and key drivers.
2. **Given** scenario and baseline differ minimally, **When** summary text is generated, **Then** the analysis explicitly notes low impact rather than overstating differences.

---

### User Story 4 - Use Prompt Hints to Improve Scenario Inputs (Priority: P3)

As a user, I receive prompt hints that guide me toward valid scenario phrasing and assumption details, reducing failed or ambiguous scenario requests.

**Why this priority**: Hints improve usability and adoption but are not required to deliver initial scenario computation.

**Independent Test**: Trigger hinting behavior with an ambiguous or incomplete what-if prompt and verify that actionable hint text is returned.

**Acceptance Scenarios**:

1. **Given** a user submits a vague scenario prompt, **When** the system cannot confidently infer assumptions, **Then** it returns prompt hints that show recommended structure and examples.
2. **Given** a user submits a complete scenario prompt, **When** the response is returned, **Then** hints are absent or minimal so they do not distract from the result.
3. **Given** a user asks about scenario capabilities (e.g., "What scenarios can you do?"), **When** the LLM classifies this as conversation with `scenario_discovery: true`, **Then** the system responds conversationally and appends interactive hint cards listing supported scenario types and example prompts.

### Edge Cases

- User mixes baseline analytics and what-if assumptions in one prompt; system must preserve intent and avoid dropping either part.
- User provides assumption values outside allowed business ranges; system must reject or constrain with a clear explanation.
- Requested scenario metric has insufficient historical signal; system must disclose limitation and avoid false precision.
- Chart rendering fails in client session; system must still return structured numeric scenario results and narrative summary.
- Multiple scenario dimensions conflict (for example, both absolute and percentage overrides on same measure); system must apply a deterministic precedence rule.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST classify user prompts for scenario intent and distinguish assumption-based what-if requests from standard conversational and descriptive analytics requests.
- **FR-002**: Scenario intent detection MUST support flexible natural language phrasing via LLM-based classification, not regex or fixed keyword patterns.
- **FR-003**: For recognized what-if requests, system MUST route processing through a scenario-capable response path that computes baseline and adjusted outcomes.
- **FR-004**: System MUST represent scenario assumptions explicitly in response payloads so users can verify what was applied.
- **FR-005**: Response MUST include interactive visualizations that compare baseline values and scenario values for requested metrics.
- **FR-016**: Interactive scenario visualizations MUST use existing native chat UI visualization capabilities (assistant-ui and tool-ui chart primitives) instead of introducing a separate custom chart rendering system for phase 1.
- **FR-006**: Response MUST include a brief explanatory analysis summarizing material differences between baseline and scenario outcomes.
- **FR-007**: Explanatory analysis MUST remain numerically consistent with returned scenario data.
- **FR-008**: System MUST provide prompt hints when scenario requests are incomplete, ambiguous, or invalid.
- **FR-009**: Prompt hints MUST include at least one example phrasing and identify missing assumption details needed to run the scenario.
- **FR-015**: Prompt hints MUST help users discover available phase-1 scenario types by listing supported assumption categories and example prompts in plain language. Discovery is triggered by LLM intent classification (via a `scenario_discovery` flag on conversation intent), not by regex pattern matching.
- **FR-010**: System MUST communicate when data support is insufficient for a requested scenario, including which required signal is missing or sparse. For phase 1, sparse is defined as either fewer than 30 baseline rows in the selected scope or fewer than 8 distinct weekly periods in the analysis window.
- **FR-011**: System MUST preserve current non-scenario chat and NL2SQL behavior when no what-if intent is detected.
- **FR-012**: System MUST record whether a response used scenario logic for observability and evaluation of routing accuracy.
- **FR-013**: Scenario responses MUST include machine-readable baseline and scenario values so downstream UI components can render charts consistently.
- **FR-014**: System MUST provide user-safe fallback output (numeric table plus concise explanation) if interactive chart payload generation is unavailable.

### Key Entities *(include if feature involves data)*

- **ScenarioIntent**: Classification output that indicates whether a user prompt requests assumption-based scenario analysis and includes confidence metadata.
- **ScenarioAssumptionSet**: User-specified and defaulted assumptions used to adjust baseline values (for example, price delta, demand delta, cost delta).
- **ScenarioComputationResult**: Structured baseline and scenario metric values, deltas, and percent-change outputs.
- **ScenarioVisualizationPayload**: Chart-ready payload describing comparison dimensions, metric series, labels, and interaction metadata.
- **ScenarioNarrativeSummary**: Short textual explanation of the most significant impacts and caveats.
- **PromptHint**: Suggested phrasing and missing-parameter guidance shown when a scenario request is under-specified.

### Assumptions

- Phase 1 focuses on assumption-based arithmetic scenarios rather than predictive or causal modeling.
- Historical transactional data in the current WideWorldImporters environment is the baseline data source for phase 1.
- Interactive charts are rendered in the existing chat experience without introducing a separate standalone dashboard.
- Prompt hints are guidance-oriented and do not replace full clarification workflows when required inputs are missing.
- Scenario discovery (asking about capabilities) is classified as conversation by the LLM, with a `scenario_discovery` flag that triggers hint card emission on the conversation response path. This avoids brittle regex-based detection.

### Dependencies

- Reliable access to current WideWorldImporters-backed metrics used for baseline computations.
- Existing chat response rendering support for interactive components in the frontend.
- Availability of native assistant-ui and tool-ui chart rendering capabilities required for scenario visualization responses.
- Existing orchestration path that can branch between conversational, standard NL2SQL, and scenario processing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In acceptance testing, at least 90% of curated what-if prompts are correctly routed to scenario processing.
- **SC-002**: In acceptance testing, at least 95% of non-scenario prompts remain in non-scenario routing.
- **SC-003**: At least 95% of successful scenario responses include both interactive chart payload and narrative summary.
- **SC-004**: At least 90% of scenario responses pass numeric consistency checks between baseline/scenario data and narrative claims.
- **SC-005**: For ambiguous scenario prompts in test set, at least 90% return actionable prompt hints instead of hard failure.
- **SC-006**: Median end-to-end response time for scenario requests remains within 20% of current comparable analytical query response time, measured on a fixed benchmark set of 30 prompts (15 scenario, 15 non-scenario analytical), with warm runs only, identical environment settings, and p50 computed over three repeated runs per prompt.
- **SC-007**: In usability testing, at least 80% of users can correctly identify at least three supported what-if scenario types after reviewing prompt hints. Discovery prompts are handled as natural conversation with appended hint cards, not routed through the scenario pipeline.
- **SC-008**: In acceptance testing, 100% of scenario chart responses are rendered through native assistant-ui/tool-ui chart-capable response components with no separate custom chart pipeline required for phase 1.
- **SC-009**: In acceptance tests containing known sparse or missing-signal prompts, at least 95% of responses correctly return insufficiency messaging that identifies the missing or sparse signal category.
