import { describe, expect, it } from "vitest";
import { chartSpecToUiBlock } from "./chart-spec-block";
import { validateProgram } from "./validator";

const rows = [
  { salesperson: "Budi", region: "Jakarta", revenue: 1200.5 },
  { salesperson: "Rizky", region: "Bandung", revenue: 3200 },
  { salesperson: "Budi", region: "Bandung", revenue: 400 },
];

function spec(mark: unknown, encoding: Record<string, unknown>, title: unknown = "Revenue") {
  return JSON.stringify({ title, mark, encoding, data: { values: rows } });
}

function built(value: string) {
  const block = chartSpecToUiBlock(value);
  expect(block).not.toBeNull();
  // Every adapted chart is a valid, evidence-bound catalog program.
  validateProgram(block!.program, block!.evidence, true);
  return block!;
}

describe("stored charts drawn with the intelligent UI", () => {
  it("keeps a sorted bar chart's order and binds its rows", () => {
    const block = built(spec("bar", {
      y: { field: "salesperson", type: "nominal", sort: "-x" },
      x: { field: "revenue", type: "quantitative" },
    }));
    expect(block.program).toBe(
      'root = Col([Chart(@Sort(@Evidence("chart"), "revenue", "desc"), "salesperson", "revenue", "bar", "Revenue")])',
    );
    expect(block.evidence.chart).toEqual(rows);
    expect(block.provenance).toMatchObject({ composer: "deterministic", evidence_ids: ["chart"] });
  });

  it("pivots a coloured category into series and maps arcs to pies", () => {
    const stacked = built(spec("bar", {
      x: { field: "salesperson", type: "nominal" },
      y: { field: "revenue", type: "quantitative" },
      color: { field: "region", type: "nominal" },
    }));
    expect(stacked.program).toContain('@Pivot(@Evidence("chart"), "salesperson", "region", "revenue")');
    expect(stacked.program).toContain('["Jakarta", "Bandung"]');
    const pie = built(spec({ type: "arc" }, {
      theta: { field: "revenue", type: "quantitative" },
      color: { field: "salesperson", type: "nominal" },
    }, { text: "Share" }));
    expect(pie.program).toBe(
      'root = Col([Chart(@Evidence("chart"), "salesperson", "revenue", "pie", "Share")])',
    );
  });

  it("counts rows itself when the stored chart counted them", () => {
    const block = built(spec("bar", {
      x: { field: "salesperson", type: "nominal" },
      y: { aggregate: "count", type: "quantitative" },
    }));
    expect(block.evidence.chart).toEqual([
      { salesperson: "Budi", count: 2 },
      { salesperson: "Rizky", count: 1 },
    ]);
    expect(block.program).toContain('"salesperson", "count", "bar"');
  });

  it("shows rows it cannot chart as a table, never as another renderer", () => {
    const block = built(spec("tick", { x: { field: "revenue", type: "quantitative" } }));
    expect(block.program).toBe(
      'root = Col([Table(@Evidence("chart"), ["salesperson", "region", "revenue"], "Revenue")])',
    );
    expect(chartSpecToUiBlock("not json")).toBeNull();
  });

  it("says a chart without rows has none instead of drawing an empty grid", () => {
    const block = chartSpecToUiBlock(JSON.stringify({ title: "Omzet per SKU", mark: "bar" }))!;
    validateProgram(block.program, block.evidence, false);
    expect(block.program).toBe(
      'root = Col([Text("Omzet per SKU"), Caption("This chart has no rows.", "muted")])',
    );
    expect(block.provenance.evidence_ids).toEqual([]);
  });

  it("preserves donut geometry in saved charts", () => {
    const block = built(spec({ type: "arc", innerRadius: 50 }, {
      theta: { field: "revenue", type: "quantitative" },
      color: { field: "salesperson", type: "nominal" },
    }));
    expect(block.program).toContain('"donut"');
  });

  it("keeps source rows when chart transforms or extra grouping cannot be represented", () => {
    const base = JSON.parse(spec("bar", {
      x: { field: "salesperson", type: "nominal" },
      y: { field: "revenue", type: "quantitative" },
    }));
    for (const value of [
      { ...base, transform: [{ filter: "datum.revenue > 1000" }] },
      { ...base, encoding: { ...base.encoding, x: { ...base.encoding.x, timeUnit: "month" } } },
      { ...base, encoding: { ...base.encoding, y: { aggregate: "count" }, color: { field: "region" } } },
    ]) {
      const block = built(JSON.stringify(value));
      expect(block.program).toContain("Table(");
      expect(block.evidence.chart).toEqual(rows);
    }
  });

  it("does not merge null and empty count categories or overwrite the category field", () => {
    const block = built(JSON.stringify({ mark: "bar", encoding: {
      x: { field: "count", type: "nominal" }, y: { aggregate: "count" },
    }, data: { values: [{ count: null }, { count: "" }, { count: "", row_count: 9 }] } }));
    expect(block.evidence.chart).toEqual([{ count: null, row_count: 1 }, { count: "", row_count: 2 }]);
    expect(chartSpecToUiBlock("[]")).toBeNull();
  });
});
