import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StudioArtifacts } from "./studio-artifacts";

const mocks = vi.hoisted(() => ({
  listArtifacts: vi.fn(),
  refreshArtifact: vi.fn(),
  deleteArtifact: vi.fn(),
}));

vi.mock("@/features/agents/api", () => ({
  studioApi: mocks,
}));

describe("Studio artifacts", () => {
  beforeEach(() => {
    mocks.listArtifacts.mockReset();
    mocks.refreshArtifact.mockReset();
    mocks.deleteArtifact.mockReset();
    mocks.listArtifacts.mockResolvedValue({
      count: 1,
      artifacts: [
        {
          artifact_id: "artifact-1",
          title: "Revenue by region",
          artifact_type: "table",
          agent_id: "agent-1",
          thread_id: "thread-1",
          database_name: "sales",
          schema_name: "analytics",
          created_at: "2026-09-21T08:00:00",
          updated_at: "2026-09-21T08:00:00",
        },
      ],
    });
    mocks.refreshArtifact.mockResolvedValue({
      artifact: {
        artifact_id: "artifact-1",
        title: "Revenue by region",
        artifact_type: "table",
        agent_id: "agent-1",
        thread_id: "thread-1",
        database_name: "sales",
        schema_name: "analytics",
        sql_text: "SELECT region, revenue FROM sales.analytics.summary",
        chart_spec: null,
        created_at: "2026-09-21T08:00:00",
        updated_at: "2026-09-21T08:00:00",
      },
      columns: ["region", "revenue"],
      rows: [["West", 120]],
      row_count: 1,
      elapsed_ms: 8,
    });
  });

  it("opens fresh data and exposes Table and SQL views", async () => {
    const screen = await render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <StudioArtifacts onSelectAgent={() => undefined} />
      </QueryClientProvider>,
    );

    await expect
      .element(screen.getByText("Revenue by region"))
      .toBeInTheDocument();
    await expect.element(screen.getByText("West")).toBeInTheDocument();

    await userEvent.click(screen.getByText("Revenue by region"));
    await expect
      .element(screen.getByRole("tab", { name: "Table" }))
      .toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "SQL" }));
    await expect
      .element(
        screen.getByText("SELECT region, revenue FROM sales.analytics.summary"),
      )
      .toBeInTheDocument();

    expect(mocks.refreshArtifact.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});
