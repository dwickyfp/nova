import { describe, expect, it } from "vitest";
import { render } from "vitest-browser-react";
import { VizView, dateTick, formatMeasure } from "./viz-view";
import type { PresModel } from "./types";

function presmodel(overrides: Partial<PresModel> = {}): PresModel {
  return {
    version: 1,
    title: "Revenue by region",
    mark: "bar",
    columns: [
      {
        name: "region",
        field: "region",
        field_type: "nominal",
        role: "dimension",
        aggregation: null,
        discrete: true,
        label: "region",
      },
      {
        name: "sum(revenue)",
        field: "revenue",
        field_type: "quantitative",
        role: "measure",
        aggregation: "sum",
        discrete: false,
        label: "sum(revenue)",
      },
    ],
    rows: [
      ["East", 391],
      ["West", 210.5],
    ],
    row_count: 2,
    axes: {
      x: {
        field: "region",
        label: "region",
        field_type: "nominal",
        discrete: true,
      },
      y: {
        field: "sum(revenue)",
        label: "sum(revenue)",
        field_type: "quantitative",
        discrete: false,
      },
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
          legend: {
            color: {
              field: "sum(revenue)",
              kind: "quantitative",
              domain: [210.5, 391],
            },
          },
        })}
      />,
    );
    const cells = screen.container.querySelectorAll("td");
    expect(cells.length).toBeGreaterThanOrEqual(4);
    await expect.element(screen.getByText("391")).toBeVisible();
  });

  it("draws a forecast and its bounds in one pane with a legend", async () => {
    const measure = (name: string) => ({
      name: `avg(${name})`,
      field: name,
      field_type: "quantitative" as const,
      role: "measure" as const,
      aggregation: "avg" as const,
      discrete: false,
      label: `avg(${name})`,
    });
    const screen = await render(
      <VizView
        presmodel={presmodel({
          title: "Forecast",
          mark: "line",
          columns: [
            {
              name: "timestamp",
              field: "timestamp",
              field_type: "temporal",
              role: "dimension",
              aggregation: null,
              discrete: true,
              label: "timestamp",
            },
            measure("prediction"),
            measure("lower_bound"),
            measure("upper_bound"),
          ],
          rows: [
            ["2026-11-05T00:00:00+00:00", 344, 255, 433],
            ["2026-11-06T00:00:00+00:00", 300, 220, 390],
          ],
          row_count: 2,
          axes: {
            x: {
              field: "timestamp",
              label: "timestamp",
              field_type: "temporal",
              discrete: true,
            },
            y: {
              field: "avg(prediction)",
              label: "avg(prediction)",
              field_type: "quantitative",
              discrete: false,
            },
          },
          totals: {},
        })}
      />,
    );
    await expect.element(screen.getByRole("listitem").first()).toBeVisible();
    expect(screen.container.querySelectorAll("li")).toHaveLength(3);
    expect(screen.container.textContent).not.toContain("AVG(");
  });

  it("explains itself when the worksheet returned no marks", async () => {
    const screen = await render(
      <VizView presmodel={presmodel({ rows: [], row_count: 0 })} />,
    );
    await expect
      .element(screen.getByText("The worksheet returned no marks."))
      .toBeVisible();
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

describe("dateTick", () => {
  it("reads a timestamp as a date", () => {
    expect(dateTick("2026-11-09T00:00:00+00:00")).toBe("9 Nov 2026");
    expect(dateTick("2026-11")).toBe("Nov 2026");
    expect(dateTick("2026-11-09 14:30:00")).toBe("9 Nov 2026 14:30");
    expect(dateTick("East")).toBe("East");
  });
});
