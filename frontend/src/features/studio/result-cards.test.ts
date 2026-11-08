import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { ResultTable } from "./result-table";
import { splitChartTitle } from "./chart-title";


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

describe("artifact actions", () => {
  it("shows a clear empty result without a misleading row-limit note", async () => {
    const screen = await render(
      createElement(ResultTable, {
        block: { title: "City total", columns: ["city", "total_amount"], rows: [] },
      }),
    );
    await expect.element(screen.getByText("The query returned no rows.")).toBeInTheDocument();
    await expect.element(screen.getByText(/Capped at the engine's row limit/)).not.toBeInTheDocument();
  });

  it("states the row cap only when the engine capped the result", async () => {
    const complete = await render(
      createElement(ResultTable, {
        block: { title: "Revenue", columns: ["total"], rows: [[42]] },
      }),
    );
    await expect.element(complete.getByText(/Capped at the engine's row limit/)).not.toBeInTheDocument();
    await cleanup();
    const capped = await render(
      createElement(ResultTable, {
        block: { title: "Revenue", columns: ["total"], rows: [[42]], truncated: true },
      }),
    );
    await expect.element(capped.getByText(/Capped at the engine's row limit/)).toBeInTheDocument();
  });

  it("shows the action it is given and no save control of its own", async () => {
    const screen = await render(
      createElement(ResultTable, {
        block: { title: "Revenue", columns: ["total"], rows: [[42]] },
        action: createElement("button", { type: "button" }, "Custom action"),
      }),
    );
    await expect.element(screen.getByRole("button", { name: "Custom action" })).toBeVisible();
    expect(screen.getByRole("button", { name: /Save table as artifact/ }).query()).toBeNull();
  });
});
