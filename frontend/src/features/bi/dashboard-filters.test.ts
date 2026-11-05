import { describe, expect, it } from "vitest";
import { countNativeFilters, withoutFilterBar } from "./dashboard-filters";

describe("dashboard filters", () => {
  it("counts Superset's native filters from the dashboard metadata", () => {
    expect(countNativeFilters({ result: { json_metadata: "{}" } })).toBe(0);
    expect(
      countNativeFilters({ result: { json_metadata: JSON.stringify({ native_filter_configuration: [{}, {}] }) } }),
    ).toBe(2);
    expect(countNativeFilters({ result: { json_metadata: "not json" } })).toBe(0);
    expect(countNativeFilters({})).toBe(0);
  });

  it("hides the filter bar without dropping the view's own parameters", () => {
    expect(withoutFilterBar("/bi/superset/dashboard/7/?standalone=2")).toBe(
      "/bi/superset/dashboard/7/?standalone=2&show_filters=0",
    );
    expect(withoutFilterBar("/bi/superset/dashboard/7/?standalone=2&show_filters=1")).toBe(
      "/bi/superset/dashboard/7/?standalone=2&show_filters=0",
    );
  });
});
