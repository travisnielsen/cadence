# Research: Assumption-Based What-If Scenarios

## R1: Flexible What-If Intent Detection

**Decision**: Extend the existing chat-host intent classification prompt to include a dedicated what-if intent class with positive and negative examples, while preserving fallback to standard NL2SQL routing for non-scenario prompts.

**Rationale**: The DataAssistant already centralizes intent triage. Adding a scenario class here provides flexible, evolvable phrasing support without introducing a second classifier service.

**Alternatives considered**:

- Keyword-only matching (`"what if"`, `"if we"`): Rejected because it is brittle and misses evolved phrasing.
- Separate scenario classifier model endpoint: Rejected as unnecessary complexity for phase 1.

## R2: Scope of Phase-1 Scenario Types

**Decision**: Support assumption categories that map directly to existing WWI signals: price deltas, demand deltas, supplier-cost deltas, and inventory-policy deltas (reorder/target).

**Rationale**: These align with validated data availability in `Sales.InvoiceLines`, `Sales.OrderLines`, `Purchasing.PurchaseOrderLines`, and `Warehouse.StockItemHoldings`.

**Alternatives considered**:

- Predictive/causal scenario modeling: Rejected for phase 1 because required signals (for example promo history) are sparse and out of scope.
- Unlimited arbitrary transformations: Rejected to avoid unclear UX and untestable contracts.

## R3: Scenario Computation Strategy

**Decision**: Use deterministic arithmetic transforms over baseline aggregates and return both baseline and scenario metrics with explicit delta fields.

**Rationale**: Deterministic transforms are transparent, testable, and consistent with phase-1 assumption-based requirements.

**Alternatives considered**:

- Pure LLM-derived numeric estimates: Rejected due to reliability and auditability concerns.
- Materialized scenario tables in SQL: Rejected for phase 1 due to persistence complexity and read-only query constraints.

## R4: Visualization Delivery Approach

**Decision**: Deliver scenario chart data as machine-readable payloads and render in chat via native assistant-ui/tool-ui chart-capable components.

**Rationale**: This satisfies FR-016 and minimizes UI architecture risk by reusing existing rendering patterns used by current tool-ui responses.

**Alternatives considered**:

- Standalone dashboard route: Rejected because feature scope is in-thread chat workflow.
- Custom chart rendering pipeline: Rejected by explicit spec constraint and maintainability concerns.

## R5: Narrative Analysis Generation

**Decision**: Build concise narrative summaries from computed result deltas, with optional LLM phrasing constrained by numeric facts.

**Rationale**: Keeps explanations aligned with returned values (FR-007) while preserving readability.

**Alternatives considered**:

- Free-form LLM narrative without numeric grounding: Rejected due to contradiction risk.
- No narrative summary: Rejected because user story 3 requires interpretive guidance.

## R6: Prompt Hints for Clarification and Discoverability

**Decision**: Use prompt hints in two modes: (1) clarification of missing assumptions and (2) discoverability of supported scenario types with examples.

**Rationale**: This directly supports FR-008, FR-009, and FR-015 and addresses user onboarding concerns.

**Alternatives considered**:

- Clarification-only hints: Rejected because users also need to know what scenario categories exist.
- Separate documentation-only approach: Rejected because in-thread guidance is needed at time of intent.

## R7: API Contract Shape for Scenario Responses

**Decision**: Add structured scenario response objects including assumptions, baseline metrics, scenario metrics, deltas, narrative summary, chart payload, and prompt hints.

**Rationale**: Strong contracts reduce backend/frontend drift and enable deterministic rendering and tests.

**Alternatives considered**:

- Reuse plain NL2SQL tabular result only: Rejected because chart/narrative/hint requirements need richer structure.
- Return chart markup only: Rejected because machine-readable data is required for fallback and testing.

## R8: Observability and Evaluation Signals

**Decision**: Record scenario routing decisions and confidence metadata at response boundaries for later evaluation of SC-001/SC-002.

**Rationale**: Success criteria are routing-quality based and need observable traces.

**Alternatives considered**:

- No explicit scenario telemetry: Rejected because success criteria could not be measured reliably.
