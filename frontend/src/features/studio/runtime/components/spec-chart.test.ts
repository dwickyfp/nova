import { describe, expect, it } from "vitest";
import { specChart } from "./spec-chart";

const rows = [
  { salesperson: "Budi", region: "Jakarta", revenue: 1200.5 },
  { salesperson: "Rizky", region: "Bandung", revenue: 3200 },
  { salesperson: "Budi", region: "Bandung", revenue: 400 },
];

function spec(mark: unknown, encoding: Record<string, unknown>, title: unknown = "Revenue") {
  return JSON.stringify({ title, mark, encoding, data: { values: rows } });
}

describe("stored charts drawn as a simple chart plan", () => {
  it("keeps a sorted bar chart's order", () => {
    const plan = specChart(spec("bar", {
      y: { field: "salesperson", type: "nominal", sort: "-x" },
      x: { field: "revenue", type: "quantitative" },
    }));
    expect(plan).toMatchObject({ kind: "chart", type: "bar", x: "salesperson", series: ["revenue"] });
    expect(plan?.rows.map((row) => row.revenue)).toEqual([3200, 1200.5, 400]);
  });

  it("pivots a coloured category into summed series and maps arcs to pies", () => {
    const stacked = specChart(spec("bar", {
      x: { field: "salesperson", type: "nominal" },
      y: { field: "revenue", type: "quantitative" },
      color: { field: "region", type: "nominal" },
    }));
    expect(stacked).toMatchObject({ kind: "chart", series: ["Jakarta", "Bandung"] });
    expect(stacked?.rows).toEqual([
      { salesperson: "Budi", Jakarta: 1200.5, Bandung: 400 },
      { salesperson: "Rizky", Bandung: 3200 },
    ]);
    const pie = specChart(spec({ type: "arc", innerRadius: 40 }, {
      theta: { field: "revenue", type: "quantitative" },
      color: { field: "salesperson", type: "nominal" },
    }, { text: "Share" }));
    expect(pie).toMatchObject({ kind: "chart", type: "donut", title: "Share", x: "salesperson" });
  });

  it("counts rows itself when the stored chart counted them", () => {
    const plan = specChart(spec("bar", {
      x: { field: "salesperson", type: "nominal" },
      y: { aggregate: "count", type: "quantitative" },
    }));
    expect(plan?.rows).toEqual([
      { salesperson: "Budi", count: 2 },
      { salesperson: "Rizky", count: 1 },
    ]);
    expect(plan).toMatchObject({ kind: "chart", series: ["count"] });
  });

  it("shows rows it cannot chart as a table instead of changing their meaning", () => {
    expect(specChart(spec("tick", { x: { field: "revenue", type: "quantitative" } }))).toMatchObject({
      kind: "table", columns: ["salesperson", "region", "revenue"],
    });
    expect(specChart(JSON.stringify({
      mark: "bar", transform: [{ filter: "datum.revenue > 1000" }],
      encoding: { x: { field: "salesperson" }, y: { field: "revenue", type: "quantitative" } },
      data: { values: rows },
    }))?.kind).toBe("table");
    expect(specChart("not json")).toBeNull();
    expect(specChart(JSON.stringify({ title: "Omzet per SKU", mark: "bar" }))).toMatchObject({
      kind: "table", rows: [],
    });
  });
});
