"use client";

import {
    DataTable,
    DataTableErrorBoundary,
    type Column,
} from "@/components/tool-ui/data-table";
import type {
    PromptHint,
    ScenarioAssumption,
    ScenarioMetricValue,
    ScenarioNarrativeSummary,
    ScenarioToolResult,
    ScenarioVisualizationPayload,
} from "@/lib/scenario-types";
import { SCENARIO_TOOL_NAME } from "@/lib/scenario-types";
import { makeAssistantToolUI, useThreadRuntime } from "@assistant-ui/react";
import {
    AlertTriangleIcon,
    ArrowDownCircleIcon,
    BarChart3Icon,
    FlaskConicalIcon,
    HelpCircleIcon,
    InfoIcon,
    LightbulbIcon,
    SlidersHorizontalIcon,
} from "lucide-react";
import { useCallback, useMemo } from "react";

// ── Interfaces ──────────────────────────────────────────────────────────

interface ScenarioArgs {
    question: string;
}

// ── Unit display helpers ────────────────────────────────────────────────

const UNIT_LABELS: Record<string, string> = {
    pct: "%",
    absolute: "",
    days: " days",
    count: "",
};

function formatAssumptionValue(assumption: ScenarioAssumption): string {
    const suffix = UNIT_LABELS[assumption.unit] ?? "";
    if (assumption.unit === "pct") {
        return `${assumption.value > 0 ? "+" : ""}${assumption.value}${suffix}`;
    }
    return `${assumption.value}${suffix}`;
}

function formatSourceBadge(source: ScenarioAssumption["source"]): string {
    switch (source) {
        case "user":
            return "User";
        case "default":
            return "Default";
        case "inferred":
            return "Inferred";
    }
}

// ── Assumptions Panel ───────────────────────────────────────────────────

function AssumptionsPanel({
    assumptions,
    scenarioType,
}: {
    assumptions: ScenarioAssumption[];
    scenarioType: string;
}) {
    if (assumptions.length === 0) return null;

    return (
        <div className="mb-3 rounded-md border border-blue-200 bg-blue-50/50 p-3 dark:border-blue-800 dark:bg-blue-950/30">
            <div className="mb-2 flex items-center gap-2">
                <SlidersHorizontalIcon className="size-4 text-blue-500" />
                <span className="text-sm font-medium text-blue-700 dark:text-blue-300">
                    Assumptions Applied
                </span>
                <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-600 dark:bg-blue-900/40 dark:text-blue-400">
                    {scenarioType}
                </span>
            </div>
            <div className="flex flex-wrap gap-2">
                {assumptions.map((a) => (
                    <span
                        key={`${a.name}-${a.scope}`}
                        className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-white px-2.5 py-1 text-xs dark:border-blue-700 dark:bg-gray-800"
                    >
                        <span className="font-medium">{a.name}</span>
                        <span className="text-blue-600 dark:text-blue-400">
                            {formatAssumptionValue(a)}
                        </span>
                        {a.scope !== "global" && (
                            <span className="text-muted-foreground">({a.scope})</span>
                        )}
                        <span className="rounded bg-gray-100 px-1 py-0.5 text-[10px] text-muted-foreground dark:bg-gray-700">
                            {formatSourceBadge(a.source)}
                        </span>
                    </span>
                ))}
            </div>
        </div>
    );
}

// ── Bar Chart (CSS-based) ───────────────────────────────────────────────

function ScenarioBarChart({
    visualization,
}: {
    visualization: ScenarioVisualizationPayload;
}) {
    const { x_key, series, rows, labels } = visualization;

    // Find baseline and scenario series
    const baselineSeries = series.find((s) => s.kind === "baseline");
    const scenarioSeries = series.find((s) => s.kind === "scenario");

    // Compute the max value across all rows for consistent bar scaling
    const maxValue = useMemo(() => {
        let max = 0;
        for (const row of rows) {
            for (const s of series) {
                if (s.kind === "delta") continue;
                const val = row[s.key];
                if (typeof val === "number" && Math.abs(val) > max) {
                    max = Math.abs(val);
                }
            }
        }
        return max || 1;
    }, [rows, series]);

    if (!baselineSeries || !scenarioSeries || rows.length === 0) {
        return null;
    }

    return (
        <div className="space-y-3">
            {/* Legend */}
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5">
                    <span className="inline-block size-3 rounded-sm bg-slate-400 dark:bg-slate-500" />
                    {labels[baselineSeries.key] ?? baselineSeries.label}
                </span>
                <span className="flex items-center gap-1.5">
                    <span className="inline-block size-3 rounded-sm bg-blue-500 dark:bg-blue-400" />
                    {labels[scenarioSeries.key] ?? scenarioSeries.label}
                </span>
            </div>

            {/* Bars */}
            <div className="space-y-2">
                {rows.map((row) => {
                    const xLabel = String(row[x_key] ?? "");
                    const baselineVal =
                        typeof row[baselineSeries.key] === "number"
                            ? (row[baselineSeries.key] as number)
                            : 0;
                    const scenarioVal =
                        typeof row[scenarioSeries.key] === "number"
                            ? (row[scenarioSeries.key] as number)
                            : 0;

                    const baselinePct = (Math.abs(baselineVal) / maxValue) * 100;
                    const scenarioPct = (Math.abs(scenarioVal) / maxValue) * 100;
                    const delta = scenarioVal - baselineVal;
                    const deltaPct =
                        baselineVal !== 0 ? (delta / baselineVal) * 100 : 0;

                    return (
                        <div key={xLabel} className="group">
                            <div className="mb-1 flex items-baseline justify-between text-xs">
                                <span className="font-medium truncate max-w-[60%]" title={xLabel}>
                                    {xLabel}
                                </span>
                                <span
                                    className={
                                        delta >= 0
                                            ? "text-green-600 dark:text-green-400"
                                            : "text-red-600 dark:text-red-400"
                                    }
                                >
                                    {delta >= 0 ? "+" : ""}
                                    {deltaPct.toFixed(1)}%
                                </span>
                            </div>
                            {/* Baseline bar */}
                            <div className="mb-0.5 flex items-center gap-2">
                                <div className="h-4 w-full rounded-sm bg-gray-100 dark:bg-gray-800">
                                    <div
                                        className="h-full rounded-sm bg-slate-400 transition-all dark:bg-slate-500"
                                        style={{ width: `${Math.max(baselinePct, 0.5)}%` }}
                                        title={`${labels[baselineSeries.key] ?? "Baseline"}: ${baselineVal.toLocaleString()}`}
                                    />
                                </div>
                                <span className="w-16 text-right text-xs tabular-nums text-muted-foreground">
                                    {baselineVal.toLocaleString()}
                                </span>
                            </div>
                            {/* Scenario bar */}
                            <div className="flex items-center gap-2">
                                <div className="h-4 w-full rounded-sm bg-gray-100 dark:bg-gray-800">
                                    <div
                                        className="h-full rounded-sm bg-blue-500 transition-all dark:bg-blue-400"
                                        style={{ width: `${Math.max(scenarioPct, 0.5)}%` }}
                                        title={`${labels[scenarioSeries.key] ?? "Scenario"}: ${scenarioVal.toLocaleString()}`}
                                    />
                                </div>
                                <span className="w-16 text-right text-xs tabular-nums text-muted-foreground">
                                    {scenarioVal.toLocaleString()}
                                </span>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

// ── Fallback Numeric Table ──────────────────────────────────────────────

const FALLBACK_COLUMNS: Column[] = [
    { key: "metric", label: "Metric", priority: "primary" },
    { key: "dimension_key", label: "Dimension" },
    { key: "baseline", label: "Baseline", format: { kind: "number", decimals: 2 } },
    { key: "scenario", label: "Scenario", format: { kind: "number", decimals: 2 } },
    { key: "delta_abs", label: "Delta", format: { kind: "delta", decimals: 2 } },
    { key: "delta_pct", label: "Change %", format: { kind: "percent", decimals: 1 } },
];

function FallbackMetricsTable({ metrics }: { metrics: ScenarioMetricValue[] }) {
    const rows = useMemo(
        () =>
            metrics.map((m) => ({
                metric: m.metric,
                dimension_key: m.dimension_key,
                baseline: m.baseline,
                scenario: m.scenario,
                delta_abs: m.delta_abs,
                delta_pct: m.delta_pct / 100,
            })),
        [metrics],
    );

    if (rows.length === 0) {
        return (
            <p className="text-sm italic text-muted-foreground">
                No metric data available
            </p>
        );
    }

    return (
        <DataTableErrorBoundary>
            <DataTable
                id="scenario-fallback-table"
                columns={FALLBACK_COLUMNS}
                data={rows}
                emptyMessage="No scenario data"
            />
        </DataTableErrorBoundary>
    );
}

// ── Narrative Summary ───────────────────────────────────────────────────

function NarrativeSummary({
    narrative,
    hasChart,
}: {
    narrative: ScenarioNarrativeSummary;
    hasChart: boolean;
}) {
    return (
        <div className="mt-3 border-t pt-3">
            <p className="text-sm font-medium">{narrative.headline}</p>
            {!hasChart && narrative.key_changes.length > 0 && (
                <ul className="mt-1.5 list-inside list-disc space-y-0.5 text-sm text-muted-foreground">
                    {narrative.key_changes.map((change) => (
                        <li key={change}>{change}</li>
                    ))}
                </ul>
            )}
            {narrative.confidence_note && (
                <p className="mt-1.5 text-xs italic text-muted-foreground">
                    {narrative.confidence_note}
                </p>
            )}
        </div>
    );
}

// ── Data Limitations ────────────────────────────────────────────────────

function DataLimitations({ limitations }: { limitations: string[] }) {
    if (limitations.length === 0) return null;

    return (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50/50 p-3 dark:border-amber-800 dark:bg-amber-950/30">
            <div className="flex items-center gap-2">
                <AlertTriangleIcon className="size-4 text-amber-500" />
                <span className="text-sm font-medium text-amber-700 dark:text-amber-300">
                    Data Limitations
                </span>
            </div>
            <ul className="mt-1.5 list-inside list-disc space-y-0.5 text-xs text-muted-foreground">
                {limitations.map((lim) => (
                    <li key={lim}>{lim}</li>
                ))}
            </ul>
        </div>
    );
}

// ── Chart Visualization (with fallback) ─────────────────────────────────

function ScenarioVisualization({
    visualization,
    metrics,
}: {
    visualization: ScenarioVisualizationPayload | undefined;
    metrics: ScenarioMetricValue[];
}) {
    // Try chart rendering when visualization payload is present
    if (visualization && visualization.rows.length > 0) {
        try {
            return <ScenarioBarChart visualization={visualization} />;
        } catch {
            // Fall through to table fallback
        }
    }

    // Fallback: numeric table (FR-014)
    return (
        <div>
            <div className="mb-2 flex items-center gap-2">
                <InfoIcon className="size-4 text-muted-foreground" />
                <span className="text-xs text-muted-foreground">
                    Showing numeric comparison table
                </span>
            </div>
            <FallbackMetricsTable metrics={metrics} />
        </div>
    );
}

// ── Prompt Hints ────────────────────────────────────────────────────────

function ClarificationHint({ hint }: { hint: PromptHint }) {
    const threadRuntime = useThreadRuntime();
    const handleExampleClick = useCallback(
        (text: string) => {
            threadRuntime.composer.setText(text);
        },
        [threadRuntime],
    );
    return (
        <div className="mb-3 rounded-md border border-amber-200 bg-amber-50/50 p-3 dark:border-amber-800 dark:bg-amber-950/30">
            <div className="mb-2 flex items-center gap-2">
                <HelpCircleIcon className="size-4 text-amber-500" />
                <span className="text-sm font-medium text-amber-700 dark:text-amber-300">
                    More Information Needed
                </span>
            </div>
            <p className="text-sm text-muted-foreground">{hint.message}</p>
            {hint.examples.length > 0 && (
                <div className="mt-2">
                    <p className="text-xs font-medium text-muted-foreground">Try something like:</p>
                    <ul className="mt-1 space-y-1">
                        {hint.examples.map((ex) => (
                            <li
                                key={ex}
                                onClick={() => handleExampleClick(ex)}
                                className="cursor-pointer rounded border border-amber-200 bg-white px-2.5 py-1.5 text-xs text-amber-800 transition hover:bg-amber-50 dark:border-amber-700 dark:bg-gray-800 dark:text-amber-200 dark:hover:bg-gray-700"
                            >
                                {ex}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}

function DiscoverabilityHint({ hint }: { hint: PromptHint }) {
    const threadRuntime = useThreadRuntime();
    const handleExampleClick = useCallback(
        (text: string) => {
            threadRuntime.composer.setText(text);
        },
        [threadRuntime],
    );
    return (
        <div className="mb-3 rounded-md border border-purple-200 bg-purple-50/50 p-3 dark:border-purple-800 dark:bg-purple-950/30">
            <div className="mb-2 flex items-center gap-2">
                <LightbulbIcon className="size-4 text-purple-500" />
                <span className="text-sm font-medium text-purple-700 dark:text-purple-300">
                    Available Scenario Types
                </span>
            </div>
            <p className="text-sm text-muted-foreground">{hint.message}</p>
            {hint.examples.length > 0 && (
                <div className="mt-2">
                    <p className="text-xs font-medium text-muted-foreground">Example prompts:</p>
                    <ul className="mt-1 space-y-1">
                        {hint.examples.map((ex) => (
                            <li
                                key={ex}
                                onClick={() => handleExampleClick(ex)}
                                className="cursor-pointer rounded border border-purple-200 bg-white px-2.5 py-1.5 text-xs text-purple-800 transition hover:bg-purple-50 dark:border-purple-700 dark:bg-gray-800 dark:text-purple-200 dark:hover:bg-gray-700"
                            >
                                {ex}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}

function DrillDownHint({ hint }: { hint: PromptHint }) {
    const threadRuntime = useThreadRuntime();
    const handleExampleClick = useCallback(
        (text: string) => {
            threadRuntime.composer.setText(text);
        },
        [threadRuntime],
    );
    return (
        <div className="mt-3 rounded-md border border-blue-200 bg-blue-50/50 p-3 dark:border-blue-800 dark:bg-blue-950/30">
            <div className="mb-2 flex items-center gap-2">
                <ArrowDownCircleIcon className="size-4 text-blue-500" />
                <span className="text-sm font-medium text-blue-700 dark:text-blue-300">
                    Drill Down
                </span>
            </div>
            <p className="text-sm text-muted-foreground">{hint.message}</p>
            {hint.examples.length > 0 && (
                <div className="mt-2">
                    <p className="text-xs font-medium text-muted-foreground">
                        Explore a specific group:
                    </p>
                    <ul className="mt-1 space-y-1">
                        {hint.examples.map((ex) => (
                            <li
                                key={ex}
                                onClick={() => handleExampleClick(ex)}
                                className="cursor-pointer rounded border border-blue-200 bg-white px-2.5 py-1.5 text-xs text-blue-800 transition hover:bg-blue-50 dark:border-blue-700 dark:bg-gray-800 dark:text-blue-200 dark:hover:bg-gray-700"
                            >
                                {ex}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}

function PromptHints({ hints, placement }: { hints: PromptHint[]; placement: "before" | "after" }) {
    if (hints.length === 0) return null;

    const filtered = hints.filter((hint) =>
        placement === "after" ? hint.kind === "drill_down" : hint.kind !== "drill_down",
    );
    if (filtered.length === 0) return null;

    return (
        <>
            {filtered.map((hint) => {
                if (hint.kind === "clarification") {
                    return (
                        <ClarificationHint
                            key={`${hint.kind}-${hint.message.slice(0, 30)}`}
                            hint={hint}
                        />
                    );
                }
                if (hint.kind === "drill_down") {
                    return (
                        <DrillDownHint
                            key={`${hint.kind}-${hint.message.slice(0, 30)}`}
                            hint={hint}
                        />
                    );
                }
                return (
                    <DiscoverabilityHint
                        key={`${hint.kind}-${hint.message.slice(0, 30)}`}
                        hint={hint}
                    />
                );
            })}
        </>
    );
}

// ── Main Tool UI ────────────────────────────────────────────────────────

export const ScenarioToolUI = makeAssistantToolUI<ScenarioArgs, ScenarioToolResult>({
    toolName: SCENARIO_TOOL_NAME,
    render: ({ result, status }) => {
        // Loading state
        if (status.type === "running") {
            return (
                <div className="flex items-center gap-3 rounded-lg border bg-muted/30 p-4 animate-pulse">
                    <FlaskConicalIcon className="size-5 text-purple-500" />
                    <span className="text-sm text-muted-foreground">
                        Running scenario analysis...
                    </span>
                </div>
            );
        }

        // Error state
        if (status.type === "incomplete" && status.reason === "error") {
            return (
                <div className="flex items-start gap-3 rounded-lg border border-destructive/50 bg-destructive/10 p-4">
                    <FlaskConicalIcon className="mt-0.5 size-5 text-destructive" />
                    <div>
                        <p className="font-medium text-destructive">Scenario Analysis Failed</p>
                        <p className="mt-1 text-sm text-muted-foreground">
                            An error occurred while processing the scenario.
                        </p>
                    </div>
                </div>
            );
        }

        // No result yet
        if (!result) {
            return null;
        }

        // Success state
        return (
            <div className="rounded-lg border bg-card p-4">
                {/* Header */}
                <div className="mb-3 flex items-center gap-2">
                    <BarChart3Icon className="size-5 text-purple-500" />
                    <span className="text-sm font-medium">
                        {result.mode === "discovery" ? "Scenario Capabilities" : "Scenario Analysis"}
                    </span>
                    {result.mode !== "discovery" && (
                        <span className="text-xs text-muted-foreground">
                            ({result.metrics.length}{" "}
                            {result.metrics.length === 1 ? "metric" : "metrics"})
                        </span>
                    )}
                </div>

                {/* Prompt hints — clarification/discoverability shown before content */}
                {result.prompt_hints && result.prompt_hints.length > 0 && (
                    <PromptHints hints={result.prompt_hints} placement="before" />
                )}

                {/* Full scenario content (hidden for discovery-only) */}
                {result.mode !== "discovery" && (
                    <>
                        {/* Assumptions */}
                        <AssumptionsPanel
                            assumptions={result.assumptions}
                            scenarioType={result.scenario_type}
                        />

                        {/* Chart or fallback table */}
                        <ScenarioVisualization
                            visualization={result.visualization ?? undefined}
                            metrics={result.metrics}
                        />

                        {/* Narrative summary */}
                        {result.narrative && (
                            <NarrativeSummary
                                narrative={result.narrative}
                                hasChart={result.visualization != null}
                            />
                        )}

                        {/* Data limitations */}
                        {result.data_limitations && result.data_limitations.length > 0 && (
                            <DataLimitations limitations={result.data_limitations} />
                        )}

                        {/* Drill-down hints — shown after chart and narrative */}
                        {result.prompt_hints && result.prompt_hints.length > 0 && (
                            <PromptHints hints={result.prompt_hints} placement="after" />
                        )}
                    </>
                )}
            </div>
        );
    },
});
