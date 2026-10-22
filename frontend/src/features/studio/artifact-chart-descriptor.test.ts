import { describe, expect, it } from "vitest";
import { descriptorToVegaLite } from "./artifact-chart-descriptor";

const columns = ["month", "revenue", "orders"];

describe("saving an interactive chart as an artifact", () => {
  it("templates a chart of the result's own columns without rows", () => {
    expect(
      descriptorToVegaLite(
        { type: "line", x: "month", y: "revenue", series: ["revenue", "orders"] },
        columns,
        "Revenue",
      ),
    ).toEqual({
      $schema: "https://vega-lite.github.io/schema/vega-lite/v5.json",
      title: "Revenue",
      mark: "line",
      width: "container",
      data: { values: [] },
      encoding: {
        x: { field: "month", type: "nominal" },
        y: { field: "revenue", type: "quantitative" },
        color: { field: "orders", type: "quantitative" },
      },
    });
    expect(
      descriptorToVegaLite({ type: "donut", x: "month", y: "revenue", series: [] }, columns, "")
        ?.encoding,
    ).toEqual({
      theta: { field: "revenue", type: "quantitative" },
      color: { field: "month", type: "nominal" },
    });
  });

  it("refuses charts of values calculated in the answer", () => {
    expect(
      descriptorToVegaLite({ type: "bar", x: "month", y: "Jakarta", series: ["Jakarta"] }, columns, ""),
    ).toBeNull();
    expect(
      descriptorToVegaLite(
        { type: "bar", x: "month", y: "revenue", series: ["revenue", "orders", "month"] },
        columns,
        "",
      ),
    ).toBeNull();
    expect(descriptorToVegaLite({ type: "gauge", x: "month", y: "revenue", series: [] }, columns, ""))
      .toBeNull();
  });
});
