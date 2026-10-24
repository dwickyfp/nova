import { describe, expect, it } from "vitest";
import type { DataPaneField, WorksheetSpec } from "./types";
import {
  applyShowMe,
  calculationDependents,
  clearSheet,
  emptyMarks,
  filterSummary,
  historyOf,
  historyReducer,
  movePill,
  pillCaption,
  pillFromField,
  pillLabel,
  placePill,
  removeCalculation,
  removePill,
  setAggregation,
  setDatePart,
  setQuickTableCalc,
  setSort,
  sortDirection,
  swapShelves,
  updatePill,
  upsertCalculation,
  viewDimensions,
} from "./worksheet-model";

const region: DataPaneField = { name: "Region", kind: "column", data_type: "string", role: "dimension", level: "row" };
const orderDate: DataPaneField = { name: "Order Date", kind: "column", data_type: "date", role: "dimension", level: "row" };
const sales: DataPaneField = { name: "Sales", kind: "column", data_type: "number", role: "measure", level: "row" };
const margin: DataPaneField = { name: "Margin", kind: "calculation", data_type: "number", role: "measure", level: "aggregate", formula: "SUM([Profit]) / SUM([Sales])" };

function sheet(overrides: Partial<WorksheetSpec> = {}): WorksheetSpec {
  return {
    version: 1,
    source: { kind: "sql", sql: "SELECT * FROM sales" },
    columns: [],
    rows: [],
    marks: emptyMarks(),
    ...overrides,
  };
}

describe("pills", () => {
  it("are built from data-pane fields the way the compiler reads them", () => {
    expect(pillFromField(region)).toEqual({ field: "Region", field_type: "nominal", discrete: true });
    expect(pillFromField(orderDate)).toMatchObject({ date_part: "year", discrete: true });
    expect(pillFromField(sales)).toMatchObject({ aggregation: "sum", discrete: false });
    expect(pillFromField(margin)).toMatchObject({ aggregation: "agg" });
  });

  it("caption and label differently: captions for people, labels for result columns", () => {
    const year = pillFromField(orderDate);
    expect(pillCaption(year)).toBe("YEAR(Order Date)");
    expect(pillLabel(year)).toBe("year(Order Date)");
    const month = setDatePart(year, "month", false);
    expect(pillLabel(month)).toBe("month_trunc(Order Date)");
    expect(pillCaption(pillFromField(margin))).toBe("AGG(Margin)");
    expect(pillLabel(pillFromField(margin))).toBe("Margin");
    expect(pillCaption({ field: "*", field_type: "quantitative", aggregation: "count", discrete: false })).toBe("CNT(Records)");
  });
});

describe("shelves", () => {
  it("place, reorder, move and remove pills", () => {
    let spec = placePill(sheet(), "columns", pillFromField(region));
    spec = placePill(spec, "columns", pillFromField(orderDate), 0);
    expect(spec.columns.map(pillLabel)).toEqual(["year(Order Date)", "Region"]);
    // Placing the same pill again moves it rather than duplicating it.
    spec = placePill(spec, "columns", pillFromField(region), 0);
    expect(spec.columns.map(pillLabel)).toEqual(["Region", "year(Order Date)"]);
    spec = movePill(spec, { target: "columns", index: 1 }, "rows");
    expect(spec.rows.map(pillLabel)).toEqual(["year(Order Date)"]);
    spec = movePill(spec, { target: "rows", index: 0 }, "color");
    expect(spec.marks.color?.field).toBe("Order Date");
    expect(spec.rows).toEqual([]);
    spec = removePill(spec, { target: "color", index: 0 });
    expect(spec.marks.color).toBeNull();
  });

  it("keep sorts and quick calcs attached when a pill changes and drop them when it goes", () => {
    let spec = placePill(sheet({ columns: [pillFromField(region)] }), "rows", pillFromField(sales));
    const location = { target: "rows" as const, index: 0 };
    spec = setSort(spec, spec.rows[0], "desc");
    spec = setQuickTableCalc(spec, spec.rows[0], "running_total");
    expect(sortDirection(spec, spec.rows[0])).toBe("desc");
    spec = updatePill(spec, location, (pill) => setAggregation(pill, "avg"));
    expect(spec.sort).toEqual([{ field: "avg(Sales)", direction: "desc" }]);
    expect(spec.table_calcs).toEqual([{ kind: "running_total", field: "avg(Sales)" }]);
    spec = removePill(spec, location);
    expect(spec.sort).toEqual([]);
    expect(spec.table_calcs).toEqual([]);
  });

  it("swap, clear and list the view's dimensions", () => {
    const spec = sheet({
      columns: [pillFromField(region)],
      rows: [pillFromField(sales)],
      marks: { ...emptyMarks(), color: pillFromField(orderDate) },
    });
    expect(swapShelves(spec).columns[0].field).toBe("Sales");
    expect(viewDimensions(spec).map(pillLabel)).toEqual(["Region", "year(Order Date)"]);
    const cleared = clearSheet(spec);
    expect(cleared.columns).toEqual([]);
    expect(cleared.marks.color).toBeNull();
  });
});

describe("calculations", () => {
  it("rename every use when a calculation is renamed", () => {
    let spec = sheet({
      rows: [pillFromField(margin)],
      calculations: [
        { name: "Margin", formula: "SUM([Profit]) / SUM([Sales])" },
        { name: "Margin %", formula: "[Margin] * 100" },
      ],
      filters: [{ kind: "quantitative", field: "Margin", min: 0 }],
    });
    spec = upsertCalculation(spec, { name: "Profit ratio", formula: "SUM([Profit]) / SUM([Sales])" }, "Margin");
    expect(spec.rows[0].field).toBe("Profit ratio");
    expect(spec.filters?.[0].field).toBe("Profit ratio");
    expect(spec.calculations?.[1].formula).toBe("[Profit ratio] * 100");
    expect(calculationDependents(spec, "Profit ratio")).toEqual(["Margin %"]);
    spec = removeCalculation(spec, "Profit ratio");
    expect(spec.rows).toEqual([]);
    expect(spec.filters).toEqual([]);
  });
});

describe("history", () => {
  it("undoes and redoes applied changes", () => {
    const first = sheet();
    const second = placePill(first, "columns", pillFromField(region));
    let state = historyReducer(historyOf(first), { type: "apply", spec: second });
    state = historyReducer(state, { type: "undo" });
    expect(state.present).toBe(first);
    state = historyReducer(state, { type: "redo" });
    expect(state.present).toBe(second);
    expect(historyReducer(state, { type: "redo" })).toBe(state);
  });
});

describe("Show Me and filters", () => {
  it("lay out a line over a continuous date", () => {
    const spec = applyShowMe(sheet(), "line", [region, orderDate, sales, margin]);
    expect(spec.marks.type).toBe("line");
    expect(spec.columns[0]).toMatchObject({ field: "Order Date", date_part: "month", discrete: false });
    expect(spec.rows.map(pillLabel)).toEqual(["sum(Sales)"]);
    expect(spec.marks.color?.field).toBe("Region");
  });

  it("summarize filters for their pill", () => {
    expect(filterSummary({ kind: "categorical", field: "Region", values: ["East", "West", "North", "South"] })).toBe(
      "East, West, North +1",
    );
    expect(filterSummary({ kind: "quantitative", field: "Sales", min: 10 })).toBe("At least 10");
    expect(filterSummary({ kind: "relative_date", field: "Order Date", range: "last_n_days", n: 7 })).toBe("Last 7 days");
  });
});
