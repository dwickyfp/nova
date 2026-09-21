import { describe, expect, it } from "vitest";
import { splitChartTitle } from "./result-cards";

/**
 * The result-card title plumbing.
 *
 * Two regressions are easy to reintroduce and both look like a blank or generic
 * header to the user: the tool sends an empty title, and the chart's title is
 * printed twice (once by the card, once by Vega). These cover the parsing that
 * prevents both.
 */

describe("splitChartTitle", () => {
  it("lifts the title out of the spec so it is rendered once", () => {
    const spec = JSON.stringify({
      title: "Omzet per SKU 3 bulan ke belakang",
      mark: "bar",
      data: { values: [] },
    });
    const { title, body } = splitChartTitle(spec);
    expect(title).toBe("Omzet per SKU 3 bulan ke belakang");
    // The chart must not carry the title again.
    expect(JSON.parse(body).title).toBeUndefined();
    expect(JSON.parse(body).mark).toBe("bar");
  });

  it("passes a spec with no title through untouched", () => {
    const spec = JSON.stringify({ mark: "bar", data: { values: [] } });
    const { title, body } = splitChartTitle(spec);
    expect(title).toBeUndefined();
    expect(body).toBe(spec);
  });

  it("treats a blank title as absent", () => {
    const spec = JSON.stringify({ title: "   ", mark: "bar" });
    const { title, body } = splitChartTitle(spec);
    expect(title).toBeUndefined();
    expect(body).toBe(spec);
  });

  it("survives an unparseable spec", () => {
    const { title, body } = splitChartTitle("not json");
    expect(title).toBeUndefined();
    expect(body).toBe("not json");
  });
});
