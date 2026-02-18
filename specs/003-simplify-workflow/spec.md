# Feature Specification: Simplify NL2SQL Workflow

**Feature Branch**: `003-simplify-workflow`
**Created**: 2026-02-17
**Status**: Draft
**Input**: User description: "Replace MAF Executor/Workflow layer with plain async Python functions while retaining ChatAgent for LLM calls"

## Context

The NL2SQL backend currently uses Microsoft Agent Framework (MAF) Executor/Workflow/WorkflowBuilder to wire together five processing stages (NL2SQLController, ParameterExtractor, ParameterValidator, QueryBuilder, QueryValidator). Analysis revealed that:

- **NL2SQLController** (1,296 lines) never makes LLM calls — it's pure routing logic wrapped in an Executor class solely to participate in the message graph.
- **ParameterValidator** (462 lines) and **QueryValidator** (384 lines) are 100% deterministic — no LLM, no I/O — yet exist as Executor subclasses.
- The Workflow message graph (WorkflowBuilder + edges) adds boilerplate and indirection for what is fundamentally a linear pipeline with one branch point (template match → ParameterExtractor **or** no match → QueryBuilder).
- `ctx.send_message()` / `ctx.yield_output()` / `ctx.shared_state` create tight coupling to a framework that provides no real orchestration value here — there is no parallelism, no fan-out, no retry logic, no conditional routing that couldn't be expressed as `if/else`.
- A fresh Workflow instance is created per request ("Agent Framework doesn't support concurrent workflow executions"), so there is no state reuse benefit.
- The ConversationOrchestrator already proves the alternative: it's a plain Python class that calls `ChatAgent` directly and works fine.

Only **two** of the five executors actually call `agent.run()` on an LLM: ParameterExtractor and QueryBuilder. Those LLM calls should be preserved — but wrapped in plain async functions instead of Executor subclasses.

### What We Keep

| MAF Component | Usage | Verdict |
|---|---|---|
| `ChatAgent` | LLM calls in Orchestrator, ParameterExtractor, QueryBuilder | **Keep** — thin wrapper over Azure AI Agent Service |
| `AzureAIClient` | Creates ChatAgent instances with Foundry connection | **Keep** — required by ChatAgent |
| `AgentThread` | Thread management for Orchestrator | **Keep** — Foundry thread lifecycle |
| `@tool` decorator | Function tools for `template_search`, `table_search`, `execute_sql` | **Keep** — registers tools with ChatAgent |
| `Executor` subclasses | 5 classes wrapping business logic | **Remove** — replace with async functions |
| `Workflow` / `WorkflowBuilder` | Message graph connecting executors | **Remove** — replace with a single `process_query()` function |
| `WorkflowContext` | `ctx.send_message()`, `ctx.shared_state` | **Remove** — replace with function args/returns |
| `@handler` / `@response_handler` | Message routing decorators | **Remove** — replace with function calls |

### Expected Outcomes

- **~60% reduction** in NL2SQL pipeline code (est. 4,744 → ~1,900 lines)
- **Zero** MAF-specific message types (`SQLDraftMessage`, etc.) in the pipeline
- **Same** Foundry agent registrations (parameter-extractor-agent, query-builder-agent)
- **Same** SSE streaming behavior — step events continue via `asyncio.Queue`
- **Same** external behavior — no API contract changes

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Extract Core Logic from Executors (Priority: P1)

Each Executor's business logic is extracted into a plain async function that takes explicit parameters and returns explicit results. The Executor class is removed. The function can be called directly without WorkflowContext.

**Why this priority**: This is the foundational change — all other stories depend on having callable functions instead of Executor subclasses. It delivers the biggest complexity reduction and unblocks everything else.

**Independent Test**: Import any extracted function (e.g., `validate_parameters()`) in a unit test, call it with test data, and verify correct output. No WorkflowContext or Workflow setup needed.

**Acceptance Scenarios**:

1. **Given** the ParameterValidator executor, **When** its logic is extracted to `validate_parameters()`, **Then** the function accepts `SQLDraft` + `list[ParameterDefinition]` and returns a validated `SQLDraft` with `params_validated=True` or a list of violations
2. **Given** the QueryValidator executor, **When** its logic is extracted to `validate_query()`, **Then** the function accepts `SQLDraft` + `AllowedTables` config and returns a validated `SQLDraft` with `query_validated=True` or a list of violations
3. **Given** the ParameterExtractor executor, **When** its logic is extracted to `extract_parameters()`, **Then** the function accepts extraction request data, calls `agent.run()` for ambiguous cases, and returns `SQLDraft` or `ClarificationRequest`
4. **Given** the QueryBuilder executor, **When** its logic is extracted to `build_query()`, **Then** the function accepts a build request, calls `agent.run()`, and returns `SQLDraft`
5. **Given** any extracted function, **When** it needs to emit progress, **Then** it accepts an optional `asyncio.Queue` for step events (same mechanism as today)
6. **Given** the NL2SQLController executor, **When** its routing logic is extracted to `process_query()`, **Then** it becomes a single async function that calls the extracted functions in sequence with `if/else` branching
7. **Given** any extracted function, **When** it is called, **Then** no reference to `Executor`, `WorkflowContext`, `@handler`, `@response_handler`, `ctx.send_message()`, `ctx.yield_output()`, or `ctx.shared_state` exists in the function body

---

### User Story 2 — Replace Workflow with Direct Function Calls (Priority: P1)

The `create_nl2sql_workflow()` function and `WorkflowBuilder` edge graph are removed. The SSE streaming endpoint (`chat.py`) calls `process_query()` directly instead of iterating `workflow.run_stream()`.

**Why this priority**: This story completes the removal of MAF's orchestration layer. Without it, the Executor extraction (US1) would still require the WorkflowBuilder to wire things together, leaving the refactor half-done.

**Independent Test**: Start the dev API server, send a query via the chat endpoint, and verify the response streams correctly with step events. Compare response structure to pre-refactor output — should be identical.

**Acceptance Scenarios**:

1. **Given** the `workflow.py` module, **When** the refactor is complete, **Then** it no longer exists (or is replaced by a simple module exporting `process_query()`)
2. **Given** `chat.py`, **When** it handles a data query, **Then** it calls `process_query()` directly and streams results via SSE without `Workflow.run_stream()`
3. **Given** `chat.py`, **When** it handles a data query, **Then** step events (template search, parameter extraction, SQL execution, etc.) still appear in the SSE stream in the same order as before
4. **Given** the `WorkflowBuilder` import, **When** the refactor is complete, **Then** no file in `src/backend/` imports `Workflow`, `WorkflowBuilder`, or `WorkflowContext`
5. **Given** the ConversationOrchestrator, **When** it invokes the NL2SQL pipeline, **Then** it calls `process_query()` with the user's question and receives `NL2SQLResponse` directly
6. **Given** a clarification scenario (missing parameters), **When** `process_query()` returns a `ClarificationRequest`, **Then** the orchestrator renders clarification options to the user without any MAF message wrapping

---

### User Story 3 — Simplify SSE Streaming and Event Plumbing (Priority: P2)

The SSE endpoint no longer needs to iterate over MAF `WorkflowEvent` objects and map them to SSE messages. Instead, it reads directly from the step event queue and the `NL2SQLResponse` returned by `process_query()`.

**Why this priority**: Once US1 and US2 are done, the SSE endpoint will already work — but it will still have residual MAF event-handling code. This story cleans that up for long-term maintainability.

**Independent Test**: Send a query and capture the raw SSE stream. Verify events are well-formed, properly ordered, and identical in structure to the pre-refactor stream.

**Acceptance Scenarios**:

1. **Given** the SSE streaming code in `chat.py`, **When** the refactor is complete, **Then** it no longer references `WorkflowEvent`, `AgentMessage`, or MAF event types
2. **Given** a successful query, **When** the SSE stream is captured, **Then** step events appear in this order: intent classification → template search → (parameter extraction | query building) → validation → SQL execution → response rendering
3. **Given** the step event mechanism, **When** `process_query()` executes, **Then** step events are emitted via the same `asyncio.Queue` pattern used today — no change to the frontend contract
4. **Given** the frontend, **When** it receives the SSE stream after the refactor, **Then** no frontend changes are needed — the event format is identical

---

### User Story 4 — Remove Unused MAF Dependencies and Wrapper Types (Priority: P2)

All MAF-specific wrapper types (e.g., `SQLDraftMessage` with `source` field for message routing) are either removed or simplified. The `agent-framework` dependency is pruned to only what `ChatAgent`, `AzureAIClient`, `AgentThread`, and `@tool` need.

**Why this priority**: Cleanup story. The system works without this, but leaving dead imports and unused types creates confusion for future contributors.

**Independent Test**: Run `uv run poe check` — no import errors, no unused imports, no type errors. Run `uv run poe test` — all tests pass.

**Acceptance Scenarios**:

1. **Given** the `SQLDraftMessage` wrapper type, **When** the refactor is complete, **Then** it is removed — `SQLDraft` is passed directly between functions without a `source` field for routing
2. **Given** the executor folder structure, **When** the refactor is complete, **Then** each entity folder contains either: (a) a module with async functions + an `agent.py` for LLM-calling entities, or (b) a module with pure functions for deterministic entities
3. **Given** `pyproject.toml`, **When** the refactor is complete, **Then** `agent-framework` and `agent-framework-azure-ai` remain as dependencies (required by ChatAgent)
4. **Given** the entire `src/backend/` tree, **When** the refactor is complete, **Then** no file imports `Executor`, `Workflow`, `WorkflowBuilder`, `WorkflowContext`, `@handler`, or `@response_handler`
5. **Given** all quality checks, **When** `uv run poe check` is run, **Then** it passes with zero errors

---

### User Story 5 — Update Tests for New Architecture (Priority: P1)

All existing tests are updated to work with the new function-based architecture. New tests are added for the `process_query()` pipeline function. Test setup is simpler because there's no Workflow/Executor boilerplate to mock.

**Why this priority**: Tests must pass for the refactor to be complete. This runs in parallel with US1/US2 — as each function is extracted, its tests are updated.

**Acceptance Scenarios**:

1. **Given** the existing test suite, **When** `uv run poe test` is run after the refactor, **Then** all tests pass
2. **Given** the extracted `validate_parameters()` function, **When** a unit test calls it directly, **Then** no WorkflowContext mock is needed — just pass `SQLDraft` and `ParameterDefinition` list
3. **Given** the `process_query()` function, **When** integration tests exercise it, **Then** they can call it directly with a mocked `ChatAgent` and verify the full pipeline output
4. **Given** any test file, **When** the refactor is complete, **Then** no test imports `Executor`, `WorkflowContext`, or `Workflow`

---

### Edge Cases

- **Clarification state**: Currently stored in `ctx.shared_state[CLARIFICATION_STATE_KEY]`. After refactor, stored as a return value from `process_query()` and passed back in on the follow-up turn (orchestrator already serializes this via `ClarificationRequest`).
- **Step event ordering**: Functions must emit step events in the same order as the current Executor graph. The `asyncio.Queue` mechanism doesn't change — only the caller changes.
- **Agent registration timing**: `ChatAgent` instances for ParameterExtractor and QueryBuilder are still created lazily and register with Foundry on first `agent.run()`. No change to registration behavior.
- **Concurrent requests**: The current system creates a fresh Workflow per request. The new system creates a fresh `process_query()` call per request — same isolation, simpler mechanism.
- **Error propagation**: Currently, executor errors propagate via `ctx.yield_output()` with error messages. After refactor, they propagate as exceptions or error return values — more Pythonic, easier to test.
- **Monitoring/tracing**: If `ENABLE_INSTRUMENTATION=true`, OpenTelemetry spans are currently attached to the Workflow. After refactor, spans attach to `process_query()` and its sub-calls — same granularity, simpler setup.

## Requirements

### Functional Requirements

- **FR-001**: The `process_query()` function MUST accept a user question, conversation context, and step event queue, and return `NL2SQLResponse` or `ClarificationRequest`
- **FR-002**: The `process_query()` function MUST search for matching templates via `template_search` tool, then branch: template found → `extract_parameters()`, no template → `build_query()`
- **FR-003**: The `validate_parameters()` function MUST be a pure function (no I/O, no LLM) that validates parameter types, ranges, regex patterns, and allowed values
- **FR-004**: The `validate_query()` function MUST be a pure function (no I/O, no LLM) that validates SQL syntax, table allowlists, and security patterns
- **FR-005**: The `extract_parameters()` function MUST support both deterministic fast-path (fuzzy matching) and LLM-assisted extraction via `ChatAgent`
- **FR-006**: The `build_query()` function MUST generate SQL via `ChatAgent` from table metadata when no template matches
- **FR-007**: All functions MUST emit step events via `asyncio.Queue` for SSE streaming progress
- **FR-008**: The SSE endpoint MUST produce identical event structure and ordering as the pre-refactor implementation
- **FR-009**: No file in `src/backend/` MUST import `Executor`, `Workflow`, `WorkflowBuilder`, `WorkflowContext`, `@handler`, or `@response_handler` after the refactor
- **FR-010**: `ChatAgent`, `AzureAIClient`, `AgentThread`, and `@tool` imports MUST be preserved — these are the retained MAF components
- **FR-011**: The `agent-framework` and `agent-framework-azure-ai` packages MUST remain in `pyproject.toml`
- **FR-012**: All quality checks (`uv run poe check`) MUST pass with zero errors
- **FR-013**: All existing tests MUST pass after the refactor, updated as needed for the new function signatures
- **FR-014**: The ConversationOrchestrator MUST continue to work unchanged — it already uses the correct pattern (plain class + ChatAgent)

### Key Entities

- **`process_query()`** (new): Top-level async function replacing NL2SQLController + Workflow. Orchestrates the full NL2SQL pipeline.
- **`extract_parameters()`** (new): Async function replacing ParameterExtractorExecutor. Calls ChatAgent for ambiguous cases.
- **`validate_parameters()`** (new): Pure function replacing ParameterValidatorExecutor. No I/O.
- **`build_query()`** (new): Async function replacing QueryBuilderExecutor. Calls ChatAgent for SQL generation.
- **`validate_query()`** (new): Pure function replacing QueryValidatorExecutor. No I/O.
- **`NL2SQLResponse`** (unchanged): Return type from `process_query()`.
- **`ClarificationRequest`** (unchanged): Alternative return type when parameters are missing.
- **`SQLDraft`** (simplified): Passed between functions directly — no `SQLDraftMessage` wrapper needed.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Total line count of NL2SQL pipeline files (excluding orchestrator) decreases by ≥ 40%
- **SC-002**: Zero imports of `Executor`, `Workflow`, `WorkflowBuilder`, `WorkflowContext`, `@handler`, `@response_handler` in `src/backend/`
- **SC-003**: All existing tests pass (`uv run poe test` exit code 0)
- **SC-004**: All quality checks pass (`uv run poe check` exit code 0)
- **SC-005**: SSE stream output for a template-based query is byte-identical in structure (event names, field names) to pre-refactor output
- **SC-006**: SSE stream output for a dynamic query is byte-identical in structure to pre-refactor output
- **SC-007**: Foundry portal shows the same agents registered (parameter-extractor-agent, query-builder-agent, conversation-orchestrator) after refactor
- **SC-008**: Unit tests for extracted pure functions (validate_parameters, validate_query) require zero mocks of framework objects
- **SC-009**: The `process_query()` function is callable from a test with only ChatAgent mocks — no Workflow setup needed
