import { describe, expect, it } from "vitest";
import { describeView, measureCaption, splitMeasure } from "./insight";
import type { PresModel } from "./types";

function model(columns: PresModel["columns"], rows: PresModel["rows"]): PresModel {
  return {
    version: 1,
    title: "",
    mark: "bar",
    columns,
    rows,
    row_count: rows.length,
    axes: { x: null, y: null },
    legend: { color: null },
    totals: {},
  };
}

const channel = { name: "SalesChannel", field: "SalesChannel", field_type: "nominal", role: "dimension", discrete: true, label: "SalesChannel" } as const;
const revenue = { name: "sum(revenue)", field: "revenue", field_type: "quantitative", role: "measure", aggregation: "sum", discrete: false, label: "sum(revenue)" } as const;
const day = { name: "visit_date", field: "visit_date", field_type: "temporal", role: "dimension", discrete: true, label: "visit_date" } as const;
const visits = { name: "sum(visits)", field: "visits", field_type: "quantitative", role: "measure", aggregation: "sum", discrete: false, label: "sum(visits)" } as const;

const CHANNELS = model([channel, revenue], [
  ["Website", 1935135942274.5],
  ["Mobile App", 3368049065451],
  ["Store", 3013024030096.5],
  ["WhatsApp B2B", 847526929200],
  ["Marketplace", 2893685878419],
]);

describe("describeView", () => {
  it("names the leader, its share and the gap to the runner-up", () => {
    const insight = describeView(CHANNELS);
    expect(insight?.lead).toEqual({ subject: "Mobile App", verb: "leads at", num: "3.37", unit: "T", marked: true });
    expect(insight?.detail).toBe("27.9% of the 12.06T total. Store is next, 355B behind; WhatsApp B2B trails at 7.0%.");
    expect(insight?.highlight).toEqual({ dimension: "SalesChannel", value: "Mobile App" });
  });

  it("reads a selected mark against the leader and the average", () => {
    const insight = describeView(CHANNELS, "Store");
    expect(insight?.lead).toEqual({ subject: "Store", verb: "ranks 2nd of 5 at", num: "3.01", unit: "T", marked: true });
    expect(insight?.detail).toBe("25.0% of 12.06T. 355B behind Mobile App, and 602B above the average of 2.41T.");
  });

  it("skips shares for averages, which do not add up", () => {
    const avg = { ...revenue, name: "avg(revenue)", aggregation: "avg", label: "avg(revenue)" } as const;
    const insight = describeView(model([channel, avg], [["A", 10], ["B", 4], ["C", 1]]));
    expect(insight?.detail).toBe("B is next, 6 behind; C is lowest at 1.");
  });

  it("describes a flat series by its range and low point", () => {
    const insight = describeView(model([day, visits], [["2026-06-22", 46], ["2026-06-23", 40], ["2026-06-24", 47]]));
    expect(insight?.lead).toEqual({ subject: "SUM(visits)", verb: "holds near", num: "44.33", unit: "", marked: false });
    expect(insight?.detail).toBe("Every point lands between 40 and 47. The low, 40 on 23 Jun 2026, sits 4.33 under the average.");
    expect(insight?.highlight?.value).toBe("2026-06-23");
  });

  it("describes a moving series by its change", () => {
    const insight = describeView(model([day, visits], [["2026-01-01", 100], ["2026-02-01", 140], ["2026-03-01", 130]]));
    expect(insight?.lead.verb).toBe("rose to");
    expect(insight?.detail).toBe("Up 30.0% from 100 on 1 Jan 2026. Peak 140 on 1 Feb 2026; low 100 on 1 Jan 2026.");
  });

  it("returns nothing when the view has no single reading", () => {
    expect(describeView(model([channel, channel, revenue], [["a", "b", 1], ["c", "d", 2]]))).toBeNull();
    expect(describeView(model([channel], [["a"]]))).toBeNull();
  });
});

describe("number parts", () => {
  it("split values into number and unit", () => {
    expect(splitMeasure(3368049065451)).toEqual({ num: "3.37", unit: "T" });
    expect(splitMeasure(6347)).toEqual({ num: "6.35", unit: "K" });
    expect(splitMeasure(0.153)).toEqual({ num: "0.15", unit: "" });
    expect(measureCaption("distinct_count(customer)")).toBe("CNTD(customer)");
    expect(measureCaption("Share of revenue")).toBe("Share of revenue");
  });
});

describe("sourceOf", () => {
  it("names the first table a query reads", async () => {
    const { sourceOf } = await import("./insight");
    expect(sourceOf("SELECT a FROM `NOVA_SALES`.`fact_sales` AS s")).toBe("NOVA_SALES.fact_sales");
    expect(sourceOf("select * from sales.orders where x > 1")).toBe("sales.orders");
    expect(sourceOf("SELECT 1")).toBeNull();
    expect(sourceOf(null)).toBeNull();
  });
});
