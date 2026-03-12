/**
 * TypeScript types for assumption-based what-if scenario analysis.
 *
 * Mirrors the backend Pydantic models in src/backend/models/scenario.py
 * and the JSON schema in specs/004-what-if-scenarios/contracts/scenario-response.schema.json.
 */

/** Tool name used by the backend when emitting scenario tool results. */
export const SCENARIO_TOOL_NAME = "scenario_analysis";

// ── Intent Classification ───────────────────────────────────────────────

export interface ScenarioIntent {
    mode: "scenario" | "standard" | "conversation";
    confidence: number;
    reason: string;
    detected_patterns: string[];
}

// ── Assumptions ─────────────────────────────────────────────────────────

export interface ScenarioAssumption {
    name: string;
    scope: string;
    value: number;
    unit: "pct" | "absolute" | "days" | "count";
    source: "user" | "default" | "inferred";
}

export interface ScenarioAssumptionSet {
    scenario_type: string;
    assumptions: ScenarioAssumption[];
    missing_requirements: string[];
    is_complete: boolean;
}

// ── Metric Values ───────────────────────────────────────────────────────

export interface ScenarioMetricValue {
    metric: string;
    dimension_key: string;
    baseline: number;
    scenario: number;
    delta_abs: number;
    delta_pct: number;
}

// ── Computation Result ──────────────────────────────────────────────────

export interface ScenarioComputationResult {
    request_id: string;
    scenario_type: string;
    metrics: ScenarioMetricValue[];
    summary_totals: Record<string, number>;
    data_limitations: string[];
}

// ── Narrative Summary ───────────────────────────────────────────────────

export interface ScenarioNarrativeSummary {
    headline: string;
    key_changes: string[];
    confidence_note: string | null;
}

// ── Visualization Payload ───────────────────────────────────────────────

export interface ChartSeriesDefinition {
    key: string;
    label: string;
    kind?: "baseline" | "scenario" | "delta";
}

export interface ScenarioVisualizationPayload {
    chart_type: "bar" | "line" | "combo";
    x_key: string;
    series: ChartSeriesDefinition[];
    rows: Record<string, string | number | boolean | null>[];
    labels: Record<string, string>;
}

// ── Prompt Hints ────────────────────────────────────────────────────────

export interface PromptHint {
    kind: "clarification" | "discoverability" | "drill_down";
    message: string;
    examples: string[];
    supported_types: string[];
}

// ── Fallback Table ──────────────────────────────────────────────────────

export interface ScenarioFallbackTable {
    columns: string[];
    rows: Record<string, string | number | boolean | null>[];
}

// ── Combined Tool Result ────────────────────────────────────────────────

/**
 * Top-level shape returned as the `result` field of a `scenario_analysis`
 * tool call in the SSE stream.  Matches the JSON schema contract at
 * specs/004-what-if-scenarios/contracts/scenario-response.schema.json.
 */
export interface ScenarioToolResult {
    mode: "scenario" | "discovery";
    scenario_type: string;
    assumptions: ScenarioAssumption[];
    metrics: ScenarioMetricValue[];
    narrative: ScenarioNarrativeSummary | null;
    visualization: ScenarioVisualizationPayload | null;
    prompt_hints: PromptHint[];
    data_limitations?: string[];
    fallback_table?: ScenarioFallbackTable | null;
}
