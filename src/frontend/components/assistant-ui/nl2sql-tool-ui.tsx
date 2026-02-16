"use client";

import {
  DataTable,
  DataTableErrorBoundary,
  sortData,
  type Column,
} from "@/components/tool-ui/data-table";
import { buildColumn } from "@/lib/column-format-rules";
import { makeAssistantToolUI, useThreadRuntime } from "@assistant-ui/react";
import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon, ChevronUpIcon, DatabaseIcon, LightbulbIcon, ShieldQuestionMark } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * NL2SQL Response structure from the backend
 */
interface NL2SQLArgs {
  question: string;
}

interface ClarificationInfo {
  parameter_name: string;
  prompt: string;
  allowed_values: string[];
}

interface NL2SQLResult {
  sql_query: string;
  sql_response: Record<string, unknown>[];
  columns: string[];
  row_count: number;
  confidence_score: number;
  used_cached_query: boolean;
  query_source?: "template" | "cached" | "dynamic";
  error?: string;
  observations?: string;
  needs_clarification?: boolean;
  clarification?: ClarificationInfo;
  defaults_used?: Record<string, string>;
  suggestions?: SchemaSuggestion[];
  // Dynamic query enhancement fields
  hidden_columns?: string[];
  query_summary?: string;
  query_confidence?: number;
  error_suggestions?: SchemaSuggestion[];
}

/**
 * Build tool-ui Column definitions from backend column names.
 *
 * Delegates format inference + width to the configurable rules module
 * (`column-format-rules.json`). First column gets mobile "primary" priority.
 */
function buildColumns(
  columnNames: string[],
  rows: Record<string, unknown>[],
): Column[] {
  return columnNames.map((name, idx) =>
    buildColumn(name, rows.map((r) => r[name]), idx === 0 ? "primary" : "secondary"),
  );
}

/**
 * Coerce row values to JSON primitives expected by tool-ui DataTable.
 * Non-primitive values are stringified.
 */
function coerceRows(
  rows: Record<string, unknown>[],
): Record<string, string | number | boolean | null>[] {
  return rows.map((row) => {
    const out: Record<string, string | number | boolean | null> = {};
    for (const [key, value] of Object.entries(row)) {
      if (value === null || value === undefined) {
        out[key] = null;
      } else if (
        typeof value === "string" ||
        typeof value === "number" ||
        typeof value === "boolean"
      ) {
        out[key] = value;
      } else {
        out[key] = String(value);
      }
    }
    return out;
  });
}

const PAGE_SIZE = 10;

/**
 * NL2SQL Data Table wrapper that maps backend response to tool-ui DataTable.
 *
 * Handles column format mapping, row coercion, pagination, and empty states.
 */
type SortState = {
  by?: string | undefined;
  direction?: "asc" | "desc" | undefined;
};

function NL2SQLDataTable({ result }: { result: NL2SQLResult }) {
  const [showAllColumns, setShowAllColumns] = useState(false);

  // Determine visible columns: when expanded, include hidden columns
  const visibleColumns = useMemo(() => {
    if (showAllColumns && result.hidden_columns && result.hidden_columns.length > 0) {
      return [...result.columns, ...result.hidden_columns];
    }
    return result.columns;
  }, [result.columns, result.hidden_columns, showAllColumns]);

  const columns = useMemo(
    () => buildColumns(visibleColumns, result.sql_response),
    [visibleColumns, result.sql_response],
  );
  const allData = useMemo(() => coerceRows(result.sql_response), [result.sql_response]);

  const [sort, setSort] = useState<SortState>({ by: undefined, direction: undefined });
  const [page, setPage] = useState(0);

  // Sort the full dataset, then paginate
  const sortedData = useMemo(() => {
    if (!sort.by || !sort.direction) return allData;
    return sortData(allData, sort.by, sort.direction);
  }, [allData, sort]);

  const totalPages = Math.ceil(sortedData.length / PAGE_SIZE);
  const pagedData = useMemo(
    () => sortedData.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [sortedData, page],
  );

  const handleSortChange = useCallback((next: SortState) => {
    setSort(next);
    setPage(0);
  }, []);

  if (result.columns.length === 0 || result.sql_response.length === 0) {
    return (
      <p className="text-sm text-muted-foreground italic">No data returned</p>
    );
  }

  return (
    <DataTableErrorBoundary>
      <DataTable
        id="nl2sql-query-results"
        columns={columns}
        data={pagedData}
        sort={sort}
        onSortChange={handleSortChange}
        emptyMessage="No data returned"
      />
      {/* Hidden columns toggle */}
      {result.hidden_columns && result.hidden_columns.length > 0 && (
        <div className="border-t px-2 py-2">
          <button
            onClick={() => setShowAllColumns(!showAllColumns)}
            className="text-sm text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
          >
            {showAllColumns
              ? "Show fewer columns"
              : `Show ${result.hidden_columns.length} more column${result.hidden_columns.length === 1 ? "" : "s"}`}
          </button>
        </div>
      )}
      {totalPages > 1 && (
        <div className="flex items-center justify-between border-t px-2 py-2 text-sm text-muted-foreground">
          <span>
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, sortedData.length)} of{" "}
            {sortedData.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded p-1 hover:bg-muted disabled:opacity-30 disabled:pointer-events-none"
              aria-label="Previous page"
            >
              <ChevronLeftIcon className="size-4" />
            </button>
            <span className="px-2 tabular-nums">
              {page + 1} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="rounded p-1 hover:bg-muted disabled:opacity-30 disabled:pointer-events-none"
              aria-label="Next page"
            >
              <ChevronRightIcon className="size-4" />
            </button>
          </div>
        </div>
      )}
    </DataTableErrorBoundary>
  );
}

/**
 * Expandable SQL Query section
 */
function SQLQuerySection({ query }: { query: string }) {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className="mt-3 border-t pt-3">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        {isExpanded ? (
          <ChevronUpIcon className="size-4" />
        ) : (
          <ChevronDownIcon className="size-4" />
        )}
        <span>Show SQL Query</span>
      </button>
      {isExpanded && (
        <pre className="mt-2 p-3 bg-muted/50 rounded-md text-xs font-mono overflow-x-auto whitespace-pre-wrap break-words">
          {query}
        </pre>
      )}
    </div>
  );
}

interface SchemaSuggestion {
  title: string;
  prompt: string;
}

/**
 * Suggestion pills for follow-up schema-area exploration
 */
function SuggestionPills({ suggestions }: { suggestions: SchemaSuggestion[] }) {
  const threadRuntime = useThreadRuntime();

  const handleClick = useCallback((prompt: string) => {
    threadRuntime.composer.setText(prompt);
    threadRuntime.composer.send();
  }, [threadRuntime]);

  return (
    <div className="mt-3 pt-3 border-t">
      <p className="text-sm font-medium text-muted-foreground mb-2">You might also explore:</p>
      <div className="flex flex-wrap gap-2">
        {suggestions.map((s) => (
          <button
            key={s.title}
            onClick={() => handleClick(s.prompt)}
            className="px-3 py-1.5 text-sm bg-white dark:bg-gray-800 border border-green-200 dark:border-green-700 rounded-full text-green-700 dark:text-green-300 hover:bg-green-100 dark:hover:bg-green-900 cursor-pointer transition-colors"
          >
            {s.title}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * Clarification options component with clickable pills
 */
function ClarificationOptions({ prompt, allowedValues }: { prompt: string; allowedValues: string[] }) {
  const threadRuntime = useThreadRuntime();

  const handleClick = useCallback((value: string) => {
    threadRuntime.composer.setText(value);
    threadRuntime.composer.send();
  }, [threadRuntime]);

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50/50 dark:border-blue-800 dark:bg-blue-950/30 p-4">
      <div className="flex items-start gap-3">
        <DatabaseIcon className="size-5 text-blue-500 mt-0.5" />
        <div className="flex-1">
          <p className="font-medium text-blue-700 dark:text-blue-300">
            I need a bit more information
          </p>
          <p className="text-sm text-muted-foreground mt-1">{prompt}</p>
          {allowedValues.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {allowedValues.map((value) => (
                <button
                  key={value}
                  onClick={() => handleClick(value)}
                  className="inline-block px-3 py-1.5 text-sm bg-white dark:bg-gray-800 border border-blue-200 dark:border-blue-700 rounded-full text-blue-700 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900 cursor-pointer transition-colors"
                >
                  {value}
                </button>
              ))}
            </div>
          )}
          <p className="text-xs text-muted-foreground mt-3">
            Click an option above or type your own
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * Confidence gate confirmation card.
 *
 * Shown when the backend returns a low-confidence dynamic query that needs
 * user approval before execution. Offers "Run this query" / "Revise" actions
 * using the same SSE composer pattern as ClarificationOptions.
 */
function ConfirmationCard({ summary, confidence }: { summary: string; confidence?: number }) {
  const threadRuntime = useThreadRuntime();

  const handleAccept = useCallback(() => {
    threadRuntime.composer.setText("yes");
    threadRuntime.composer.send();
  }, [threadRuntime]);

  const handleRevise = useCallback(() => {
    threadRuntime.composer.setText("revise");
    threadRuntime.composer.send();
  }, [threadRuntime]);

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-950/30 p-4">
      <div className="flex items-start gap-3">
        <ShieldQuestionMark className="size-5 text-amber-500 mt-0.5" />
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <p className="font-medium text-amber-700 dark:text-amber-300">
              Confirm query
            </p>
            {confidence !== undefined && confidence > 0 && (
              <span className="text-xs bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 px-2 py-0.5 rounded">
                {Math.round(confidence * 100)}% confidence
              </span>
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-1">{summary}</p>
          <div className="mt-3 flex gap-2">
            <button
              onClick={handleAccept}
              className="px-3 py-1.5 text-sm bg-amber-600 text-white rounded-md hover:bg-amber-700 dark:bg-amber-700 dark:hover:bg-amber-600 cursor-pointer transition-colors"
            >
              Run this query
            </button>
            <button
              onClick={handleRevise}
              className="px-3 py-1.5 text-sm bg-white dark:bg-gray-800 border border-amber-200 dark:border-amber-700 rounded-md text-amber-700 dark:text-amber-300 hover:bg-amber-100 dark:hover:bg-amber-900 cursor-pointer transition-colors"
            >
              Revise
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * NL2SQL Tool UI Component
 *
 * Renders the results of an NL2SQL query with:
 * 1. Data table at the top
 * 2. Expandable SQL query section
 * 3. Observations/commentary at the bottom
 * 4. Custom database icon to indicate this is a tool response
 */
export const NL2SQLToolUI = makeAssistantToolUI<NL2SQLArgs, NL2SQLResult>({
  toolName: "nl2sql_query",
  render: ({ args, result, status }) => {
    // Loading state
    if (status.type === "running") {
      return (
        <div className="flex items-center gap-3 p-4 rounded-lg border bg-muted/30 animate-pulse">
          <DatabaseIcon className="size-5 text-blue-500" />
          <span className="text-sm text-muted-foreground">
            Querying database...
          </span>
        </div>
      );
    }

    // Error state
    if (status.type === "incomplete" && status.reason === "error") {
      return (
        <div className="flex items-start gap-3 p-4 rounded-lg border border-destructive/50 bg-destructive/10">
          <DatabaseIcon className="size-5 text-destructive mt-0.5" />
          <div>
            <p className="font-medium text-destructive">Query Failed</p>
            <p className="text-sm text-muted-foreground mt-1">
              {result?.error || "An error occurred while executing the query"}
            </p>
          </div>
        </div>
      );
    }

    // No result yet
    if (!result) {
      return null;
    }

    // Clarification needed - friendly prompt with clickable options
    if (result.needs_clarification && result.clarification) {
      const { prompt, allowed_values } = result.clarification;
      return (
        <ClarificationOptions prompt={prompt} allowedValues={allowed_values} />
      );
    }

    // Confidence gate confirmation — low-confidence dynamic query awaiting approval
    if (result.needs_clarification && result.query_summary) {
      return (
        <ConfirmationCard summary={result.query_summary} confidence={result.query_confidence} />
      );
    }

    // Error in result
    if (result.error) {
      return (
        <div className="flex items-start gap-3 p-4 rounded-lg border border-destructive/50 bg-destructive/10">
          <DatabaseIcon className="size-5 text-destructive mt-0.5" />
          <div className="flex-1">
            <p className="font-medium text-destructive">Query Error</p>
            <p className="text-sm text-muted-foreground mt-1">{result.error}</p>
            {result.sql_query && <SQLQuerySection query={result.sql_query} />}
            {/* Error recovery suggestions */}
            {result.error_suggestions && result.error_suggestions.length > 0 && (
              <SuggestionPills suggestions={result.error_suggestions} />
            )}
          </div>
        </div>
      );
    }

    // Success state
    return (
      <div className="rounded-lg border bg-card p-4">
        {/* Header with icon and row count */}
        <div className="flex items-center gap-2 mb-3">
          <DatabaseIcon className="size-5 text-blue-500" />
          <span className="text-sm font-medium">
            Query Results
          </span>
          <span className="text-xs text-muted-foreground">
            ({result.row_count} {result.row_count === 1 ? "row" : "rows"})
          </span>
          {/* Query source badge */}
          {(result.query_source === "template" || result.query_source === "cached") && (
            <span className="ml-auto text-xs bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 px-2 py-0.5 rounded">
              Verified Query
            </span>
          )}
          {result.query_source === "dynamic" && (
            <span className="ml-auto text-xs bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 px-2 py-0.5 rounded">
              Custom Query
            </span>
          )}
          {/* Fallback for backward compatibility when query_source is not set */}
          {!result.query_source && result.used_cached_query && (
            <span className="ml-auto text-xs bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 px-2 py-0.5 rounded">
              Verified Query
            </span>
          )}
        </div>

        {/* Defaults used note */}
        {result.defaults_used && Object.keys(result.defaults_used).length > 0 && (
          <div className="text-sm text-muted-foreground italic mb-3">
            Using {Object.keys(result.defaults_used).length === 1 ? "default" : "defaults"}:{" "}
            {Object.values(result.defaults_used).join(", ")}
          </div>
        )}

        {/* Data Table */}
        <NL2SQLDataTable result={result} />

        {/* Expandable SQL Query */}
        {result.sql_query && <SQLQuerySection query={result.sql_query} />}

        {/* Observations/Commentary */}
        {result.observations && (
          <div className="mt-3 pt-3 border-t">
            <div className="flex items-center gap-2 mb-2">
              <LightbulbIcon className="size-4 text-amber-500" />
              <span className="text-base font-medium">Insights</span>
            </div>
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <Markdown remarkPlugins={[remarkGfm]}>{result.observations}</Markdown>
            </div>
          </div>
        )}

        {/* Suggestion Pills */}
        {result.suggestions && result.suggestions.length > 0 && (
          <SuggestionPills suggestions={result.suggestions} />
        )}
      </div>
    );
  },
});
