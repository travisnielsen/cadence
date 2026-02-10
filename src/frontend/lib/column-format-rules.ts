/**
 * Column format inference configuration.
 *
 * Loads rules from `column-format-rules.json` and exposes helpers
 * that `nl2sql-tool-ui.tsx` uses to decide how columns are displayed.
 *
 * To change formatting behaviour, edit the JSON file — no code changes needed.
 *
 * ## JSON structure
 *
 * | Key                | Type                       | Purpose                                          |
 * | ------------------ | -------------------------- | ------------------------------------------------ |
 * | `currencyPatterns`  | `string[]`                | Substrings that flag a numeric column as currency |
 * | `defaultCurrency`   | `string`                  | ISO 4217 code used for inferred currency columns  |
 * | `columnOverrides`   | `Record<string, Override>` | Exact-match overrides keyed by column name        |
 *
 * ### Column overrides
 *
 * ```jsonc
 * "columnOverrides": {
 *   "AvgOrderValue": { "format": { "kind": "currency", "currency": "USD", "decimals": 2 } },
 *   "OrderDate":     { "format": { "kind": "date", "dateFormat": "short" } },
 *   "IsActive":      { "format": { "kind": "boolean" } }
 * }
 * ```
 *
 * Overrides take priority over pattern-based inference.
 */

import type { Column, FormatConfig } from "@/components/tool-ui/data-table";
import rules from "./column-format-rules.json";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ColumnOverride {
    format: FormatConfig;
    width?: string;
}

interface ColumnFormatRules {
    currencyPatterns: string[];
    defaultCurrency: string;
    columnOverrides: Record<string, ColumnOverride>;
}

// ---------------------------------------------------------------------------
// Compiled rules (evaluated once at module load)
// ---------------------------------------------------------------------------

const config: ColumnFormatRules = {
    currencyPatterns: rules.currencyPatterns ?? [],
    defaultCurrency: rules.defaultCurrency ?? "USD",
    columnOverrides: (rules.columnOverrides ?? {}) as Record<string, ColumnOverride>,
};

/** Regex built from the JSON pattern list — case-insensitive. */
const currencyRe: RegExp = new RegExp(
    config.currencyPatterns.join("|"),
    "i",
);

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Resolve a compact column width for known format kinds.
 * Returns undefined for text/unknown columns so they share remaining space.
 */
export function inferWidth(fmt: FormatConfig | undefined): string | undefined {
    if (!fmt) return undefined;
    switch (fmt.kind) {
        case "number":
        case "currency":
        case "percent":
        case "delta":
            return "140px";
        case "boolean":
            return "100px";
        case "date":
            return "150px";
        default:
            return undefined;
    }
}

/**
 * Infer a FormatConfig from column name + sample data values.
 *
 * Resolution order:
 * 1. Exact-match override from `columnOverrides`
 * 2. Currency regex from `currencyPatterns`
 * 3. Generic number (auto-detect decimals)
 * 4. `undefined` (plain text)
 */
export function inferFormat(
    name: string,
    values: unknown[],
): FormatConfig | undefined {
    // 1. Explicit override wins
    const override = config.columnOverrides[name];
    if (override) return override.format;

    // 2-4. Data-driven inference (only for numeric columns)
    const nonNull = values.filter((v) => v !== null && v !== undefined);
    if (nonNull.length === 0) return undefined;
    if (!nonNull.every((v) => typeof v === "number")) return undefined;

    const nums = nonNull as number[];
    const hasFraction = nums.some((n) => !Number.isInteger(n));
    const decimals = hasFraction ? 2 : 0;

    if (currencyRe.test(name)) {
        return { kind: "currency", currency: config.defaultCurrency, decimals };
    }
    return { kind: "number", decimals };
}

/**
 * Build a Column definition for a single column, applying overrides and inference.
 *
 * Resolution: explicit override → pattern inference → plain text.
 * Width is set from override or inferred from format kind.
 */
export function buildColumn(
    name: string,
    values: unknown[],
    priority: "primary" | "secondary" = "secondary",
): Column {
    const override = config.columnOverrides[name];
    const fmt = override?.format ?? inferFormat(name, values);
    const width = override?.width ?? inferWidth(fmt);

    const col: Column = { key: name, label: name, priority };
    if (fmt) col.format = fmt;
    if (width) col.width = width;
    return col;
}
