# Quickstart: Assumption-Based What-If Scenarios

## Goal

Implement phase-1 what-if scenarios in the existing chat flow with native assistant-ui/tool-ui visualization support.

## Prerequisites

- Backend environment configured (`src/backend/.env`)
- Frontend dependencies installed (`src/frontend`)
- Access to WideWorldImporters-backed SQL dataset

## Implementation Sequence

1. Update orchestrator intent routing
- Extend assistant intent prompt and classification handling to recognize flexible what-if phrasing.
- Preserve existing non-scenario routing behavior.

2. Add scenario response contracts
- Introduce/extend Pydantic models for assumptions, computed metrics, chart payload, narrative summary, and hints.
- Ensure models are suitable for SSE tool-result emission.

3. Add scenario computation path
- Integrate scenario branch into pipeline flow.
- Compute baseline + scenario values using deterministic arithmetic transforms.
- Include data-limitation messaging where signal is sparse.

4. Add prompt hint behavior
- Clarification hints for missing assumptions.
- Discoverability hints listing supported phase-1 scenario categories with examples.

5. Add frontend rendering with native primitives
- Extend assistant tool-ui rendering to recognize scenario payloads.
- Render chart output with assistant-ui/tool-ui native chart-capable components.
- Support FR-014 fallback rendering when chart payload cannot render.

6. Add tests
- Backend unit tests: intent routing, assumption validation, computation correctness, narrative consistency.
- Integration tests: end-to-end scenario tool payloads.
- Frontend tests (or component checks): chart render path and fallback path.

## Validation Commands

```bash
cd /home/trniel/cadence
uv run poe check      # Runs format, lint, typecheck, test
uv run poe metrics    # Check complexity and dead code
uv run poe test       # Run tests only
```

Frontend lint/build checks:

```bash
cd /home/trniel/cadence/src/frontend
pnpm lint
pnpm build
```

## Latency Validation Protocol (SC-006)

### Automated Benchmark (In-Process)

Run the benchmark test scaffold in the integration test suite:

```bash
cd /home/trniel/cadence
uv run poe test -- -m benchmark tests/integration/test_workflow_integration.py -v
```

This executes `TestScenarioLatencyBenchmark` which:
- Runs 15 simulated scenario requests and 15 analytical requests
- Discards a warm-up pass
- Collects 3 measured passes and computes p50 latencies
- Asserts: scenario p50 ≤ 1.2× analytical p50

### Full End-to-End Benchmark (Manual)

For a full network-level benchmark against a running API server:

1. Use a fixed benchmark corpus of 30 prompts:
   - 15 scenario prompts (covering all 4 supported types)
   - 15 comparable non-scenario analytical prompts

2. Keep environment settings identical for all benchmark runs:
   - Same backend/frontend build and configuration
   - Same dataset and query-template state

3. Run one warm-up pass and discard warm-up metrics.

4. Run three measured passes over the full corpus and capture per-request end-to-end API latency (measure from request send to final SSE event received).

5. Compute p50 latencies separately for:
   - Scenario prompt class
   - Non-scenario analytical baseline class

6. Validate SC-006 threshold:
   - **Pass** if scenario median ≤ 1.2× analytical median
   - **Fail** if scenario median > 1.2× analytical median

7. Save benchmark inputs and output summary artifacts with test evidence so results are reproducible.

### Example Benchmark Prompts

**Scenario prompts:**
- "What if we raise prices by 5% for top categories?"
- "What if demand increases 20% next quarter?"
- "Assume supplier costs rise 8%, show impact on margin."
- "What if we increase reorder points by 25%?"

**Analytical prompts (non-scenario):**
- "Show top 10 customers by revenue"
- "What are the total sales by category this year?"
- "List purchase orders from last month"
- "Show stock items below reorder level"

## Prompt-Hint Usability Validation (SC-007)

### Purpose

Validate that users can correctly identify at least 3 supported what-if scenario types after reviewing prompt hints. This covers SC-007: ≥80% of users identify ≥3 scenario types.

### Supported Scenario Categories

The system supports these phase-1 scenario types (defined in `src/backend/shared/scenario_constants.py`):

| Category               | Constant                       | Example Prompt                                 |
|-----------------------|-------------------------------|-------------------------------------------------|
| Price changes          | `price_delta`                  | "What if we raise prices by 10%?"               |
| Demand changes         | `demand_delta`                 | "What if demand increases 20% next quarter?"     |
| Supplier cost changes  | `supplier_cost_delta`          | "What if supplier costs go up 8%?"               |
| Inventory policy changes | `inventory_policy_delta`     | "What if we increase reorder points by 25%?"     |

### Test Prompts for Discoverability Hints

Submit these prompts and verify the response includes a discoverability hint listing the supported categories:

1. **"Show me what-if options"** → Should return a discoverability hint listing all 4 categories with example prompts.
2. **"What scenarios can I explore?"** → Same expected discoverability response.
3. **"Help me with scenario analysis"** → Should trigger discoverability guidance.

### Test Prompts for Clarification Hints

Submit these incomplete prompts and verify clarification hints are returned:

1. **"What if prices change?"** → Should return clarification hint asking for a specific percentage value (missing: `price_delta_pct`).
2. **"Run a demand scenario"** → Should return clarification hint asking for demand delta value.
3. **"Assume supplier costs change"** → Missing specific cost delta value.

### Expected Hint Structure

Each prompt hint should contain:
- `kind`: `"clarification"` or `"discoverability"`
- `message`: Human-readable guidance describing what's missing or available
- `examples`: At least one example prompt showing correct phrasing
- `supported_types`: List of applicable scenario type identifiers

### Validation Procedure

1. Start the dev API server: `uv run poe dev-api`
2. Submit each test prompt via the chat UI or API
3. Verify responses contain the expected hints with all required fields
4. Confirm that after reviewing discoverability hints, a user can name at least 3 of the 4 supported scenario types

## Manual Verification

- Scenario detection examples:
  - "What if we increase prices by 5% for top categories?"
  - "Assume supplier costs rise 8%, show impact on margin."
- Discoverability hint example:
  - "Show me what-if options" should list supported scenario categories and sample prompts.
- UI rendering:
  - Scenario response displays chart + concise narrative summary in-thread.
  - If chart render fails, numeric table + summary still displayed (FR-014).

## Expected Deliverables

- Updated backend routing and scenario contracts
- Scenario-capable response payloads
- Native assistant-ui/tool-ui scenario visualization integration
- Prompt hint discoverability + clarification behavior
- Passing quality and test gates
