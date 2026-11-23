/**
 * Studio component registry: the one schema source for components the model
 * composes answers with.
 *
 * Each schema here is exported to JSON Schema by
 * `scripts/build-component-schemas.mjs` into
 * `backend/app/modules/assistant/ui_components.json`. The backend validates
 * every component it emits and every tool argument against that file, and
 * builds the model's tool definitions from it, so the browser, the server and
 * the model read the same contract. `pnpm components:check` fails on drift.
 *
 * Keep this module free of React and browser imports: the generator bundles
 * it for Node.
 */
import { z } from "zod";

export const COMPONENTS_VERSION = "nova-components-1";

const id = z.string().regex(/^[A-Za-z0-9_.:-]{1,128}$/);
const columnName = z.string().min(1).max(256);

/** A native Dataviz chart drawn from a governed draft query. */
export const datavizChartProps = z
  .object({
    title: z.string().min(1).max(256),
    /**
     * A native `ChartDefinition` (schema version 2). Its dataset is the draft
     * named by `draft_ref`; the server checks it with the Dataviz contract.
     */
    chart: z.record(z.string(), z.unknown()),
    /** Signed, caller-bound reference to the chart's governed query. */
    draft_ref: z.string().min(8).max(2048),
  })
  .strict();

const kpiItem = z
  .object({
    label: z.string().min(1).max(120),
    /** Read by the server from verified result rows, never written by the model. */
    value: z.number().nullable(),
    format: z.enum(["number", "currency", "percent"]).default("number"),
    currency: z.string().regex(/^[A-Z]{3}$/).optional(),
    decimals: z.number().int().min(0).max(6).optional(),
    /** How the value was taken from its rows. */
    aggregation: z.enum(["value", "sum", "avg", "min", "max", "count"]),
    evidence_id: id,
    column: columnName,
  })
  .strict();

/** A row of headline numbers, each read from one verified result. */
export const kpiTilesProps = z
  .object({
    title: z.string().max(256).optional(),
    items: z.array(kpiItem).min(1).max(6),
  })
  .strict();

/** A point or shape map over one verified result's rows. */
export const geoMapProps = z
  .object({
    title: z.string().min(1).max(256),
    evidence_id: id,
    longitude: columnName.optional(),
    latitude: columnName.optional(),
    geometry: columnName.optional(),
    label: columnName.optional(),
    /** Up to 200 rows of the result, already redacted by the server. */
    rows: z.array(z.record(z.string(), z.unknown())).max(200),
  })
  .strict();

/** Arguments of the `data_to_chart` tool: how to show one verified result. */
export const dataToChartArgs = z
  .object({
    intent: z
      .string()
      .max(1000)
      .optional()
      .describe("What the view should show, e.g. 'revenue by month'. Used when no fields are given."),
    evidence_id: id
      .optional()
      .describe("The verified result to show; defaults to the latest data result."),
    title: z.string().max(120).optional().describe("A short business title, e.g. 'Revenue by month'."),
    visual: z
      .enum(["auto", "bar", "line", "area", "pie", "scatter", "table", "kpi"])
      .optional()
      .describe("bar: rankings and comparisons. line or area: trends over time. pie: share of a whole with few parts. kpi: headline numbers from the result. auto: let Nova choose."),
    x: columnName
      .optional()
      .describe("The category or date column, spelled exactly as in the result."),
    date_grain: z
      .enum(["day", "week", "month", "quarter", "year"])
      .optional()
      .describe("For a date x: the period each point stands for."),
    y: z
      .array(columnName)
      .min(1)
      .max(4)
      .optional()
      .describe("The measure columns. For kpi, each becomes one tile."),
    aggregation: z
      .enum(["sum", "avg", "min", "max", "count", "count_distinct"])
      .optional()
      .describe("How rows that share an x value combine. Defaults to sum."),
    color: columnName.optional().describe("A second category that splits the series."),
    sort: z
      .object({ field: columnName, direction: z.enum(["asc", "desc"]) })
      .strict()
      .optional(),
    top_n: z.number().int().min(1).max(50).optional().describe("Keep only the largest N categories."),
  })
  .strict();

type Entry = { version: number; description: string; schema: z.ZodType };

export const COMPONENTS = {
  DatavizChart: {
    version: 1,
    description: "A native Dataviz chart bound to the governed query behind a verified result.",
    schema: datavizChartProps,
  },
  KpiTiles: {
    version: 1,
    description: "Headline numbers read from verified result rows.",
    schema: kpiTilesProps,
  },
  GeoMap: {
    version: 1,
    description: "A map of the locations in a verified result.",
    schema: geoMapProps,
  },
} as const satisfies Record<string, Entry>;

export const TOOLS = {
  data_to_chart: {
    description:
      "Show a verified data result as a native Dataviz chart or as headline KPI tiles. " +
      "Name the visual and the exact result columns (x, y); Nova reads every value from the " +
      "result and binds the chart to its governed query. Call it after a query, or for a " +
      "follow-up about the preceding result. Do not call it before a data result exists. " +
      "The chart stays a draft in the answer: the person may edit it and decide to save it to " +
      "a dashboard. You cannot save it, so never say it was saved.",
    schema: dataToChartArgs,
  },
} as const;

export type ComponentName = keyof typeof COMPONENTS;
export type DatavizChartProps = z.infer<typeof datavizChartProps>;
export type KpiTilesProps = z.infer<typeof kpiTilesProps>;
export type GeoMapProps = z.infer<typeof geoMapProps>;

function jsonSchema(schema: z.ZodType) {
  const { $schema: _schema, ...rest } = z.toJSONSchema(schema, { target: "draft-2020-12", io: "input" }) as Record<string, unknown>;
  return rest;
}

/** The exported contract, as written to the backend. */
export function componentContract() {
  return {
    version: COMPONENTS_VERSION,
    components: Object.fromEntries(
      Object.entries(COMPONENTS).map(([name, entry]) => [
        name,
        { version: entry.version, description: entry.description, props: jsonSchema(entry.schema) },
      ]),
    ),
    tools: Object.fromEntries(
      Object.entries(TOOLS).map(([name, entry]) => [
        name,
        { description: entry.description, parameters: jsonSchema(entry.schema) },
      ]),
    ),
  };
}
