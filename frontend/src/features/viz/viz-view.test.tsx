import { describe, expect, it } from "vitest";
import { render } from "vitest-browser-react";
import { VizView, formatMeasure } from "./viz-view";
import type { PresModel } from "./types";

function presmodel(overrides: Partial<PresModel> = {}): PresModel {
  return {
    version: 1,
    title: "Revenue by region",
    mark: "bar",
    columns: [
      { name: "region", field: "region", field_type: "nominal", role: "dimension", aggregation: null, discrete: true, label: "region" },
      { name: "sum(revenue)", field: "revenue", field_type: "quantitative", role: "measure", aggregation: "sum", discrete: false, label: "sum(revenue)" },
    ],
    rows: [
      ["East", 391],
      ["West", 210.5],
    ],
    row_count: 2,
    axes: {
      x: { field: "region", label: "region", field_type: "nominal", discrete: true },
      y: { field: "sum(revenue)", label: "sum(revenue)", field_type: "quantitative", discrete: false },
    },
    legend: { color: null },
    totals: { "sum(revenue)": 601.5 },
    ...overrides,
  };
}

describe("VizView", () => {
  it("renders a bar mark from the presentation model", async () => {
    const screen = await render(<VizView presmodel={presmodel()} />);
    // recharts measures its container before drawing, so the browser test
    // asserts the view mounted with its data rather than SVG axis text.
    await expect
      .element(screen.getByRole("region", { name: "Revenue by region" }))
      .toBeInTheDocument();
  });

  it("renders the text mark as a shaded crosstab", async () => {
    const screen = await render(
      <VizView
        presmodel={presmodel({
          mark: "text",
          legend: { color: { field: "sum(revenue)", kind: "quantitative", domain: [210.5, 391] } },
        })}
      />,
    );
    const cells = screen.container.querySelectorAll("td");
    expect(cells.length).toBeGreaterThanOrEqual(4);
    await expect.element(screen.getByText("391")).toBeVisible();
  });

  it("explains itself when the worksheet returned no marks", async () => {
    const screen = await render(<VizView presmodel={presmodel({ rows: [], row_count: 0 })} />);
    await expect.element(screen.getByText("The worksheet returned no marks.")).toBeVisible();
  });
});

describe("formatMeasure", () => {
  it("abbreviates large numbers", () => {
    expect(formatMeasure(950)).toBe("950");
    expect(formatMeasure(0.15)).toBe("0.15");
    expect(formatMeasure(0.1)).toBe("0.1");
    expect(formatMeasure(12.34)).toBe("12.34");
    expect(formatMeasure(3)).toBe("3");
    expect(formatMeasure(1200)).toBe("1.2K");
    expect(formatMeasure(3_368_049_065_451)).toBe("3.37T");
    expect(formatMeasure(2_400_000)).toBe("2.4M");
    expect(formatMeasure(5_100_000_000)).toBe("5.1B");
    expect(formatMeasure(null)).toBe("–");
  });
});
