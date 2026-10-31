import { describe, expect, it } from "vitest";
import type { PresModel } from "@/features/viz/types";
import {
  filterArtifactRows,
  filterPresmodel,
  inferArtifactColumnType,
  type ArtifactFilter,
} from "./artifact-filters";

const filter = (
  column: string,
  operator: ArtifactFilter["operator"],
  value = "",
  secondValue = "",
): ArtifactFilter => ({
  id: `${column}-${operator}`,
  column,
  operator,
  value,
  secondValue,
});

describe("artifact filters", () => {
  const columns = ["channel", "revenue", "created_at"];
  const rows = [
    ["Website", "120.50", "2026-09-01"],
    ["Store", "85.00", "2026-09-02"],
    [null, "200.00", "2026-09-03"],
  ];

  it("infers numeric strings and dates from refreshed query results", () => {
    expect(inferArtifactColumnType(rows, 0)).toBe("text");
    expect(inferArtifactColumnType(rows, 1)).toBe("number");
    expect(inferArtifactColumnType(rows, 2)).toBe("date");
  });

  it("combines typed rules with all or any semantics", () => {
    const rules = [
      filter("revenue", "greater_than", "100"),
      filter("channel", "contains", "web"),
    ];
    expect(filterArtifactRows(columns, rows, rules, "all")).toEqual([rows[0]]);
    expect(filterArtifactRows(columns, rows, rules, "any")).toEqual([
      rows[0],
      rows[2],
    ]);
  });

  it("supports inclusive ranges, dates, and empty cells", () => {
    expect(
      filterArtifactRows(
        columns,
        rows,
        [filter("revenue", "between", "85", "120.5")],
        "all",
      ),
    ).toEqual([rows[0], rows[1]]);
    expect(
      filterArtifactRows(
        columns,
        rows,
        [filter("created_at", "greater_or_equal", "2026-09-02")],
        "all",
      ),
    ).toEqual([rows[1], rows[2]]);
    expect(
      filterArtifactRows(columns, rows, [filter("channel", "is_empty")], "all"),
    ).toEqual([rows[2]]);
  });

  it("keeps incomplete and unavailable rules from hiding data", () => {
    expect(
      filterArtifactRows(
        columns,
        rows,
        [filter("revenue", "between", "100")],
        "all",
      ),
    ).toEqual(rows);
    expect(
      filterArtifactRows(
        columns,
        rows,
        [filter("missing", "equals", "x")],
        "all",
      ),
    ).toEqual(rows);
  });
});

describe("filterPresmodel", () => {
  const presmodel: PresModel = {
    version: 1,
    title: "Outlets by type",
    mark: "bar",
    columns: [
      { name: "outlet_type", field: "outlet_type", field_type: "nominal", role: "dimension", discrete: true, label: "outlet_type" },
      { name: "sum(outlets)", field: "outlets", field_type: "quantitative", role: "measure", aggregation: "sum", discrete: false, label: "sum(outlets)" },
      { name: "avg(score)", field: "score", field_type: "quantitative", role: "measure", aggregation: "avg", discrete: false, label: "avg(score)" },
    ],
    rows: [["Warung", 113, 4], ["Toko Kelontong", 62, 3], ["Toko Grosir", 23, 5]],
    row_count: 3,
    axes: { x: null, y: null },
    legend: { color: null },
    totals: { "sum(outlets)": 198, "avg(score)": 4 },
  };

  it("narrows the chart's rows by a dimension and keeps additive totals", () => {
    const narrowed = filterPresmodel(presmodel, [filter("outlet_type", "contains", "toko")], "all");
    expect(narrowed?.rows).toEqual([["Toko Kelontong", 62, 3], ["Toko Grosir", 23, 5]]);
    expect(narrowed?.row_count).toBe(2);
    expect(narrowed?.totals).toEqual({ "sum(outlets)": 85, "avg(score)": null });
  });

  it("gives up on a filter over a column the chart aggregates", () => {
    expect(filterPresmodel(presmodel, [filter("outlets", "greater_than", "50")], "all")).toBeNull();
  });
});
