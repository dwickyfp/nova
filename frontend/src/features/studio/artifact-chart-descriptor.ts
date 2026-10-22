import type { UiArtifactRequest } from "./intelligent-ui/types";

const MARKS: Record<string, string> = {
  bar: "bar",
  line: "line",
  area: "area",
  pie: "arc",
  donut: "arc",
  scatter: "point",
};

/**
 * The saved-artifact Vega-Lite template for an interactive chart.
 *
 * Artifacts re-run their SQL and redraw the rows it returns, so a chart is
 * saveable only when it plots the result's own columns: at most two measures,
 * the second shown as colour. Charts of locally derived columns return null.
 */
export function descriptorToVegaLite(
  chart: NonNullable<UiArtifactRequest["chart"]>,
  columns: string[],
  title: string,
): Record<string, unknown> | null {
  const mark = MARKS[chart.type];
  const measures = chart.series.length ? chart.series : [chart.y];
  const known = new Set(columns);
  if (!mark || measures.length > 2 || ![chart.x, ...measures].every((c) => known.has(c)))
    return null;
  const quantitative = (field: string) => ({ field, type: "quantitative" });
  let encoding: Record<string, unknown>;
  if (mark === "arc") {
    encoding = {
      theta: quantitative(measures[0]),
      color: { field: chart.x, type: "nominal" },
    };
  } else if (mark === "point") {
    encoding = { x: quantitative(chart.x), y: quantitative(measures[0]) };
  } else {
    encoding = {
      x: { field: chart.x, type: "nominal" },
      y: quantitative(measures[0]),
      ...(measures[1] ? { color: quantitative(measures[1]) } : {}),
    };
  }
  return {
    $schema: "https://vega-lite.github.io/schema/vega-lite/v5.json",
    title,
    mark,
    width: "container",
    data: { values: [] },
    encoding,
  };
}
