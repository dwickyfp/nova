/**
 * Stored Vega-Lite charts (tool charts, older answers, saved artifacts) read
 * as a plain chart plan: rows, a category, measure series and a mark.
 *
 * Only what a simple chart can say is drawn. A spec that transforms, bins,
 * layers or facets its rows is shown as those rows instead, because dropping
 * the transformation would plot different numbers than the stored chart.
 */
type Records = Record<string, unknown>[];
type Channel = { field: string; type?: unknown; sort?: unknown };

export type ChartKind = "bar" | "line" | "area" | "pie" | "donut" | "scatter";
export type SpecChart =
  | { kind: "chart"; title: string; rows: Records; x: string; series: string[]; type: ChartKind }
  | { kind: "table"; title: string; rows: Records; columns: string[] };

const MARKS: Record<string, ChartKind> = {
  bar: "bar",
  line: "line",
  area: "area",
  arc: "pie",
  point: "scatter",
  circle: "scatter",
  square: "scatter",
};
const MAX_SERIES = 8;

function titleOf(spec: Record<string, unknown>): string {
  const title = spec.title;
  if (typeof title === "string") return title;
  if (title && typeof title === "object" && typeof (title as { text?: unknown }).text === "string")
    return (title as { text: string }).text;
  return "";
}

function plan(spec: Record<string, unknown>, records: Records, title: string): SpecChart {
  const columns = [...new Set(records.flatMap((row) => Object.keys(row)))];
  const known = new Set(columns);
  const encoding = (spec.encoding && typeof spec.encoding === "object"
    ? spec.encoding
    : {}) as Record<string, unknown>;
  const mark = spec.mark && typeof spec.mark === "object"
    ? (spec.mark as { type?: unknown }).type
    : spec.mark;
  const kind: ChartKind | undefined = mark === "arc" && typeof spec.mark === "object" && spec.mark
    && Number((spec.mark as { innerRadius?: unknown }).innerRadius) > 0
    ? "donut" : MARKS[String(mark)];
  const channel = (name: string): Channel | null => {
    const value = encoding[name] as Record<string, unknown> | undefined;
    return value && typeof value.field === "string" && known.has(value.field) && !value.aggregate
      ? (value as Channel)
      : null;
  };
  const counted = (name: string) =>
    (encoding[name] as { aggregate?: unknown } | undefined)?.aggregate === "count";
  const chart = (rows: Records, x: string, series: string[], type: ChartKind): SpecChart =>
    ({ kind: "chart", title, rows, x, series, type });
  const table = (): SpecChart => ({ kind: "table", title, rows: records, columns });

  if (!records.length) return table();
  // These declarations change the plotted values or their grouping. Showing
  // the original rows is safer than silently discarding their semantics.
  if (spec.transform || spec.layer || spec.facet || spec.repeat
    || ["x2", "y2", "row", "column", "detail"].some((key) => encoding[key])
    || Object.values(encoding).some((value) => value && typeof value === "object"
      && ("bin" in value || "timeUnit" in value || "condition" in value))) return table();

  if (kind && (counted("x") || counted("y") || counted("theta"))) {
    // Vega counts rows itself; the counts are computed here so the chart plots them.
    const category = channel("x") ?? channel("y") ?? channel("color");
    if (category) {
      const color = channel("color");
      if (color && color.field !== category.field) return table();
      const name = category.field === "count" ? "row_count" : "count";
      const counts = new Map<unknown, number>();
      for (const row of records) {
        const key = row[category.field] ?? null;
        counts.set(key, (counts.get(key) ?? 0) + 1);
      }
      const rows = [...counts].map(([key, value]) => ({ [category.field]: key, [name]: value }));
      return chart(rows, category.field, [name], kind === "scatter" ? "bar" : kind);
    }
  }
  if (kind === "pie" || kind === "donut") {
    const theta = channel("theta");
    const color = channel("color");
    if (theta && color) return chart(records, color.field, [theta.field], kind);
  } else if (kind === "scatter") {
    const x = channel("x");
    const y = channel("y");
    if (x && y && x.type === "quantitative" && y.type === "quantitative")
      return chart(records, x.field, [y.field], "scatter");
  } else if (kind) {
    const first = channel("x");
    const second = channel("y");
    if (first && second) {
      const [measure, category] = first.type === "quantitative" ? [first, second] : [second, first];
      if (measure.type === "quantitative" && category.type !== "quantitative") {
        const color = encoding.color as Record<string, unknown> | undefined;
        const colorField = typeof color?.field === "string" && known.has(color.field)
          && !color.aggregate ? color.field : null;
        if (colorField && color?.type === "quantitative")
          return chart(records, category.field, [measure.field, colorField], kind);
        if (colorField) {
          const groups = [...new Set(records.map((row) => row[colorField])
            .filter((value) => value != null).map(String))];
          if (groups.length && groups.length <= MAX_SERIES)
            return chart(pivot(records, category.field, colorField, measure.field),
              category.field, groups, kind);
        } else {
          const rows = String(category.sort ?? "").startsWith("-")
            ? [...records].sort((a, b) => Number(b[measure.field] ?? 0) - Number(a[measure.field] ?? 0))
            : records;
          return chart(rows, category.field, [measure.field], kind);
        }
      }
    }
  }
  // Anything a simple chart cannot say is still shown, as its rows.
  return table();
}

/** One row per category with one column per group, summing the measure. */
function pivot(rows: Records, category: string, group: string, measure: string): Records {
  const out = new Map<unknown, Record<string, unknown>>();
  for (const row of rows) {
    const key = row[category] ?? null;
    const name = row[group];
    if (name == null || ["__proto__", "constructor", "prototype"].includes(String(name))) continue;
    const target = out.get(key) ?? { [category]: key };
    out.set(key, target);
    const value = Number(row[measure]);
    if (Number.isFinite(value)) target[String(name)] = Number(target[String(name)] ?? 0) + value;
  }
  return [...out.values()];
}

/** The chart plan for a stored Vega-Lite spec and its rows, or null if unreadable. */
export function specChart(chartSpec: string): SpecChart | null {
  let spec: unknown;
  try {
    spec = JSON.parse(chartSpec);
  } catch {
    return null;
  }
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) return null;
  const values = ((spec as { data?: { values?: unknown } }).data?.values ?? []) as unknown;
  const rows = (Array.isArray(values) ? values : []).filter(
    (row): row is Record<string, unknown> => !!row && typeof row === "object" && !Array.isArray(row),
  );
  return plan(spec as Record<string, unknown>, rows, titleOf(spec as Record<string, unknown>));
}

/** A cell of the result grid for any stored value. */
export function cellOf(value: unknown): string | number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" || typeof value === "number") return value;
  return typeof value === "boolean" ? String(value) : JSON.stringify(value);
}
