import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PresModel, VizComputeResponse, WorksheetSpec } from "./types";
import { WorksheetEditor } from "./worksheet-editor";

const mocks = vi.hoisted(() => ({
  describeVizSource: vi.fn(),
  computeViz: vi.fn(),
  suggestViz: vi.fn(),
  updateArtifactSpec: vi.fn(),
  checkVizCalculation: vi.fn(),
  vizFunctions: vi.fn(),
  vizFieldDomain: vi.fn(),
}));

vi.mock("@/features/agents/api", () => ({ studioApi: mocks }));

const FIELDS = [
  { name: "region", kind: "column", data_type: "string", role: "dimension", level: "row" },
  { name: "order_date", kind: "column", data_type: "date", role: "dimension", level: "row" },
  { name: "sales", kind: "column", data_type: "number", role: "measure", level: "row" },
] as const;

const spec: WorksheetSpec = {
  version: 1,
  title: "Sales by region",
  source: { kind: "sql", sql: "SELECT * FROM sales" },
  columns: [{ field: "region", field_type: "nominal", discrete: true }],
  rows: [{ field: "sales", field_type: "quantitative", aggregation: "sum", discrete: false }],
  marks: { type: "bar" },
};

function presmodel(measure = "sum(sales)"): PresModel {
  return {
    version: 1,
    title: "Sales by region",
    mark: "bar",
    columns: [
      { name: "region", field: "region", field_type: "nominal", role: "dimension", discrete: true, label: "region" },
      { name: measure, field: "sales", field_type: "quantitative", role: "measure", discrete: false, label: measure },
    ],
    rows: [
      ["East", 350],
      ["West", 530],
    ],
    row_count: 2,
    axes: {
      x: { field: "region", label: "region", field_type: "nominal", discrete: true },
      y: { field: measure, label: measure, field_type: "quantitative", discrete: false },
    },
    legend: { color: null },
    totals: {},
  };
}

function computed(): VizComputeResponse {
  return {
    presmodel: presmodel("avg(sales)"),
    sql: "SELECT `region`, AVG(`sales`) AS `avg(sales)` FROM (SELECT * FROM sales) AS nova_source GROUP BY `region`",
    columns: ["region", "avg(sales)"],
    rows: [],
    row_count: 2,
    elapsed_ms: 3,
    fields: [...FIELDS],
  };
}

async function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WorksheetEditor
        artifactId="chart-1"
        spec={spec}
        initialPresmodel={presmodel()}
        title="Sales by region"
        expectedUpdatedAt="2026-10-24T08:00:00"
        onSaved={() => undefined}
        onCancel={() => undefined}
      />
    </QueryClientProvider>,
  );
}

describe("WorksheetEditor", () => {
  beforeEach(() => {
    mocks.describeVizSource.mockReset().mockResolvedValue({
      fields: [...FIELDS],
      sample_columns: ["region", "order_date", "sales"],
      sample_rows: [["East", "2024-01-01", 10]],
    });
    mocks.computeViz.mockReset().mockResolvedValue(computed());
    mocks.suggestViz.mockReset().mockResolvedValue({ suggestions: [] });
    mocks.updateArtifactSpec.mockReset().mockResolvedValue({});
    mocks.vizFunctions.mockReset().mockResolvedValue({ functions: [] });
    mocks.checkVizCalculation.mockReset();
    mocks.vizFieldDomain.mockReset();
  });

  it("lists the data pane by role and shows the saved view's pills", async () => {
    const screen = await mount();
    const pane = screen.getByRole("complementary", { name: "Data pane" });
    await expect.element(pane.getByText("region")).toBeVisible();
    await expect.element(pane.getByText("Records (count)")).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "SUM(sales) options" })).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "region options" })).toBeVisible();
  });

  it("changes a measure's aggregation from its pill menu and recomputes", async () => {
    const screen = await mount();
    await screen.getByRole("button", { name: "SUM(sales) options" }).click();
    await screen.getByRole("menuitem", { name: /Measure \(Sum\)/ }).click();
    await screen.getByRole("menuitemradio", { name: "Average" }).click();
    await expect.element(screen.getByRole("button", { name: "AVG(sales) options" })).toBeVisible();
    // The mount computes the saved view first; wait for the recompute.
    await vi.waitFor(() => {
      const calls = mocks.computeViz.mock.calls;
      const body = calls[calls.length - 1]?.[0] as { spec: WorksheetSpec } | undefined;
      expect(body?.spec.rows[0].aggregation).toBe("avg");
    });
    await screen.getByRole("button", { name: "Generated SQL" }).click();
    await expect.element(screen.getByText(/AVG\(`sales`\)/)).toBeVisible();
  });

  it("adds a field from its menu, undoes it, and saves the worksheet", async () => {
    const screen = await mount();
    const pane = screen.getByRole("complementary", { name: "Data pane" });
    await pane.getByRole("button", { name: "Actions for order_date" }).click();
    await screen.getByRole("menuitem", { name: "Columns" }).click();
    await expect.element(screen.getByRole("button", { name: "YEAR(order_date) options" })).toBeVisible();
    await screen.getByRole("button", { name: "Undo" }).click();
    await expect.element(screen.getByRole("button", { name: "YEAR(order_date) options" })).not.toBeInTheDocument();
    await screen.getByRole("button", { name: "Save sheet" }).click();
    await vi.waitFor(() => expect(mocks.updateArtifactSpec).toHaveBeenCalled());
    const [id, body] = mocks.updateArtifactSpec.mock.calls[0] as [string, { viz_spec: WorksheetSpec }];
    expect(id).toBe("chart-1");
    expect(body.viz_spec.columns.map((pill) => pill.field)).toEqual(["region"]);
  });

  it("shows why a view cannot compute instead of a blank canvas", async () => {
    mocks.computeViz.mockReset().mockRejectedValue(new Error("[Margin]: Unknown function NOPE."));
    const screen = await mount();
    await screen.getByRole("button", { name: "Swap rows and columns" }).click();
    await expect.element(screen.getByText("[Margin]: Unknown function NOPE.")).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Save sheet" })).toBeDisabled();
  });
});
