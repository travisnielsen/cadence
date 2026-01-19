"use client";

import { makeAssistantToolUI, useThreadRuntime } from "@assistant-ui/react";
import { DatabaseIcon, ChevronDownIcon, ChevronUpIcon, LightbulbIcon } from "lucide-react";
import { useState, useCallback } from "react";
import { cn } from "@/lib/utils";
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
}

/**
 * Data Table component for rendering SQL results
 */
function DataTable({
  columns,
  rows,
  maxRows = 10,
}: {
  columns: string[];
  rows: Record<string, unknown>[];
  maxRows?: number;
}) {
  const displayRows = rows.slice(0, maxRows);
  const hasMore = rows.length > maxRows;

  if (columns.length === 0 || rows.length === 0) {
    return (
      <p className="text-sm text-muted-foreground italic">No data returned</p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b bg-muted/50">
            {columns.map((col) => (
              <th
                key={col}
                className="px-3 py-2 text-left font-medium text-muted-foreground"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {displayRows.map((row, idx) => (
            <tr
              key={idx}
              className={cn(
                "border-b transition-colors",
                idx % 2 === 0 ? "bg-background" : "bg-muted/30"
              )}
            >
              {columns.map((col) => (
                <td key={col} className="px-3 py-2">
                  {String(row[col] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {hasMore && (
        <p className="mt-2 text-xs text-muted-foreground">
          Showing {maxRows} of {rows.length} rows
        </p>
      )}
    </div>
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

    // Error in result
    if (result.error) {
      return (
        <div className="flex items-start gap-3 p-4 rounded-lg border border-destructive/50 bg-destructive/10">
          <DatabaseIcon className="size-5 text-destructive mt-0.5" />
          <div>
            <p className="font-medium text-destructive">Query Error</p>
            <p className="text-sm text-muted-foreground mt-1">{result.error}</p>
            {result.sql_query && <SQLQuerySection query={result.sql_query} />}
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

        {/* Data Table */}
        <DataTable columns={result.columns} rows={result.sql_response} />

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
      </div>
    );
  },
});
