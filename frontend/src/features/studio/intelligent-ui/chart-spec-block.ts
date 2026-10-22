import { UI_CATALOG_VERSION } from "./catalog";
import type { UiBlock } from "./types";

type Records = Record<string, unknown>[];
type Channel = { field: string; type?: unknown; sort?: unknown };

const MARKS: Record<string, string> = {
  bar: "bar",
  line: "line",
  area: "area",
  arc: "pie",
  point: "scatter",
  circle: "scatter",
  square: "scatter",
};
const SOURCE = "chart";
const MAX_SERIES = 8;

const text = (value: string) => JSON.stringify(value);

function hash(value: string): string {
  let h = 5381;
  for (let i = 0; i < value.length; i += 1) h = (h * 33) ^ value.charCodeAt(i);
  return `local-${(h >>> 0).toString(16)}`;
}

function titleOf(spec: Record<string, unknown>): string {
  const title = spec.title;
  if (typeof title === "string") return title;
  if (title && typeof title === "object" && typeof (title as { text?: unknown }).text === "string")
    return (title as { text: string }).text;
  return "";
}

function program(
  spec: Record<string, unknown>,
  records: Records,
  title: string,
): { program: string; records: Records } {
  const columns = new Set(records.flatMap((row) => Object.keys(row)));
  const encoding = (spec.encoding && typeof spec.encoding === "object"
    ? spec.encoding
    : {}) as Record<string, unknown>;
  const mark = spec.mark && typeof spec.mark === "object"
    ? (spec.mark as { type?: unknown }).type
    : spec.mark;
  const kind = mark === "arc" && typeof spec.mark === "object" && spec.mark
    && Number((spec.mark as { innerRadius?: unknown }).innerRadius) > 0
    ? "donut" : MARKS[String(mark)];
  const channel = (name: string): Channel | null => {
    const value = encoding[name] as Record<string, unknown> | undefined;
    return value && typeof value.field === "string" && columns.has(value.field) && !value.aggregate
      ? (value as Channel)
      : null;
  };
  const counted = (name: string) =>
    (encoding[name] as { aggregate?: unknown } | undefined)?.aggregate === "count";
  const data = `@Evidence(${text(SOURCE)})`;
  const chart = (source: string, x: string, y: string, type: string, series: string[] = []) =>
    `root = Col([Chart(${[source, text(x), text(y), text(type), text(title),
      ...(series.length ? [`[${series.map(text).join(", ")}]`] : [])].join(", ")})])`;

  if (!records.length)
    return {
      program: `root = Col([Text(${text(title || "Chart")}), Caption("This chart has no rows.", "muted")])`,
      records,
    };

  const table = () => ({
    program: `root = Col([Table(${data}, [${[...columns].map(text).join(", ")}], ${text(title || "Chart data")})])`,
    records,
  });
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
      return { program: chart(data, category.field, name, kind === "scatter" ? "bar" : kind), records: rows };
    }
  }
  if (kind === "pie" || kind === "donut") {
    const theta = channel("theta");
    const color = channel("color");
    if (theta && color) return { program: chart(data, color.field, theta.field, kind), records };
  } else if (kind === "scatter") {
    const x = channel("x");
    const y = channel("y");
    if (x && y && x.type === "quantitative" && y.type === "quantitative")
      return { program: chart(data, x.field, y.field, "scatter"), records };
  } else if (kind) {
    const first = channel("x");
    const second = channel("y");
    if (first && second) {
      const [measure, category] = first.type === "quantitative" ? [first, second] : [second, first];
      if (measure.type === "quantitative" && category.type !== "quantitative") {
        const color = encoding.color as Record<string, unknown> | undefined;
        const colorField = typeof color?.field === "string" && columns.has(color.field)
          && !color.aggregate ? color.field : null;
        if (colorField && color?.type === "quantitative")
          return {
            program: chart(data, category.field, measure.field, kind, [measure.field, colorField]),
            records,
          };
        if (colorField) {
          const groups = [...new Set(records.map((row) => row[colorField])
            .filter((value) => value != null).map(String))];
          if (groups.length && groups.length <= MAX_SERIES)
            return {
              program: chart(
                `@Pivot(${data}, ${text(category.field)}, ${text(colorField)}, ${text(measure.field)})`,
                category.field, groups[0], kind, groups,
              ),
              records,
            };
        } else {
          const source = String(category.sort ?? "").startsWith("-")
            ? `@Sort(${data}, ${text(measure.field)}, "desc")`
            : data;
          return { program: chart(source, category.field, measure.field, kind), records };
        }
      }
    }
  }
  // Anything a catalog chart cannot say is still shown, as its rows.
  return table();
}

/**
 * The interactive block for a stored Vega-Lite chart and its rows.
 *
 * Tool charts, saved artifacts and dashboard tiles keep Vega-Lite as their data
 * template; Studio draws all of them with the intelligent UI components.
 */
export function chartSpecToUiBlock(chartSpec: string): UiBlock | null {
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
  const title = titleOf(spec as Record<string, unknown>);
  const built = program(spec as Record<string, unknown>, rows, title);
  return {
    catalog_version: UI_CATALOG_VERSION,
    program_sha256: hash(built.program + JSON.stringify(built.records)),
    program: built.program,
    fallback: title ? `**${title}**` : "Chart",
    evidence: built.records.length ? { [SOURCE]: built.records } : {},
    state_defaults: {},
    actions: [],
    provenance: {
      evidence_ids: built.records.length ? [SOURCE] : [],
      scope: "verified_result_rows",
      assumption: false,
      composer: "deterministic",
    },
  };
}
