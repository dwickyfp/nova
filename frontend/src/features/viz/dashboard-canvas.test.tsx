import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DashboardCanvas } from "./dashboard-canvas";
import type { DashboardComputeResponse, DashboardSpec } from "./types";

const mocks = vi.hoisted(() => ({
  computeVizDashboard: vi.fn(),
  updateArtifactSpec: vi.fn(),
}));

vi.mock("@/features/agents/api", () => ({
  studioApi: mocks,
}));

const charts = [
  {
    artifact_id: "chart-1",
    title: "Revenue",
    artifact_type: "chart" as const,
    agent_id: null,
    thread_id: null,
    database_name: null,
    schema_name: null,
    created_at: "2026-10-24T07:00:00",
    updated_at: "2026-10-24T07:00:00",
  },
  {
    artifact_id: "chart-2",
    title: "Trend",
    artifact_type: "chart" as const,
    agent_id: null,
    thread_id: null,
    database_name: null,
    schema_name: null,
    created_at: "2026-10-24T07:00:00",
    updated_at: "2026-10-24T07:00:00",
  },
];

const spec: DashboardSpec = {
  version: 1,
  title: "Overview",
  zones: [
    { zone_id: "z1", kind: "sheet", artifact_id: "chart-1", rect: { x: 0, y: 0, w: 12, h: 2 } },
    { zone_id: "z2", kind: "sheet", artifact_id: "chart-2", rect: { x: 12, y: 0, w: 12, h: 2 } },
  ],
};

function computeResponse(): DashboardComputeResponse {
  return {
    title: "Overview",
    zones: [
      {
        zone_id: "z1",
        artifact_id: "chart-1",
        title: "Revenue",
        presmodel: {
          version: 1,
          title: "Revenue",
          mark: "bar",
          columns: [
            { name: "region", field: "region", field_type: "nominal", role: "dimension", aggregation: null, discrete: true, label: "region" },
            { name: "sum(revenue)", field: "revenue", field_type: "quantitative", role: "measure", aggregation: "sum", discrete: false, label: "sum(revenue)" },
          ],
          rows: [["West", 210]],
          row_count: 1,
          axes: {
            x: { field: "region", label: "region", field_type: "nominal", discrete: true },
            y: { field: "sum(revenue)", label: "sum(revenue)", field_type: "quantitative", discrete: false },
          },
          legend: { color: null },
          totals: { "sum(revenue)": 210 },
        },
        sql: "SELECT 1",
        row_count: 1,
      },
      { zone_id: "z2", artifact_id: "chart-2", error: "This chart is no longer available." },
    ],
  };
}

async function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DashboardCanvas
        artifactId="dash-1"
        spec={spec}
        charts={charts}
        expectedUpdatedAt="2026-10-24T08:00:00"
      />
    </QueryClientProvider>,
  );
}

describe("DashboardCanvas", () => {
  beforeEach(() => {
    mocks.computeVizDashboard.mockReset().mockResolvedValue(computeResponse());
    mocks.updateArtifactSpec.mockReset().mockResolvedValue({});
  });

  it("places sheet zones on the grid and reports a broken sheet without blanking the canvas", async () => {
    const screen = await mount();
    await expect.element(screen.getByText("Revenue")).toBeVisible();
    await expect
      .element(screen.getByText("This chart is no longer available."))
      .toBeVisible();
    expect(mocks.computeVizDashboard).toHaveBeenCalled();
    const call = mocks.computeVizDashboard.mock.calls[0][0] as { spec: { zones: unknown[] } };
    expect(call.spec.zones).toHaveLength(2);
  });
});
