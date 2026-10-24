import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ArtifactExplorer } from "./artifact-explorer";

const mocks = vi.hoisted(() => ({
  listArtifacts: vi.fn(),
  refreshArtifact: vi.fn(),
  getArtifact: vi.fn(),
  computeVizDashboard: vi.fn(),
  createArtifact: vi.fn(),
  deleteArtifact: vi.fn(),
}));

vi.mock("@/features/agents/api", () => ({ studioApi: mocks }));

const base = {
  agent_id: null,
  thread_id: null,
  database_name: null,
  schema_name: null,
};
const ARTIFACTS = [
  { ...base, artifact_id: "c1", title: "Revenue trend", artifact_type: "chart", created_at: "2026-10-20T08:00:00", updated_at: "2026-10-22T08:00:00" },
  { ...base, artifact_id: "d1", title: "Sales overview", artifact_type: "dashboard", created_at: "2026-10-21T08:00:00", updated_at: "2026-10-24T08:00:00" },
  { ...base, artifact_id: "c2", title: "Accounts by region", artifact_type: "chart", created_at: "2026-10-23T08:00:00", updated_at: "2026-10-23T08:00:00" },
];

async function mount(onOpen = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const screen = await render(
    <QueryClientProvider client={client}>
      <ArtifactExplorer onOpen={onOpen} />
    </QueryClientProvider>,
  );
  return { screen, onOpen };
}

describe("ArtifactExplorer", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mocks.listArtifacts.mockReset().mockResolvedValue({ artifacts: ARTIFACTS, count: 3 });
    mocks.refreshArtifact.mockReset().mockResolvedValue({ artifact: {}, columns: [], rows: [], row_count: 0, elapsed_ms: 1 });
    mocks.getArtifact.mockReset().mockResolvedValue({
      ...ARTIFACTS[1],
      sql_text: "",
      chart_spec: null,
      viz_spec: { version: 1, zones: [{ zone_id: "z1", kind: "text", rect: { x: 0, y: 0, w: 24, h: 1 }, text: { content: "Quarterly sales", style: "title" } }] },
    });
    mocks.computeVizDashboard.mockReset().mockResolvedValue({ title: "", zones: [] });
    mocks.createArtifact.mockReset();
    mocks.deleteArtifact.mockReset();
  });

  it("filters by content type and draws dashboard zones in the thumbnail", async () => {
    const { screen } = await mount();
    await expect.element(screen.getByText("Revenue trend")).toBeVisible();
    await screen.getByRole("button", { name: /^Dashboards/ }).click();
    await expect.element(screen.getByText("Sales overview")).toBeVisible();
    await expect.element(screen.getByText("Revenue trend")).not.toBeInTheDocument();
    await expect.element(screen.getByText("Quarterly sales")).toBeInTheDocument();
  });

  it("sorts the list view by name and keeps favorites", async () => {
    const { screen, onOpen } = await mount();
    await screen.getByRole("button", { name: "List view" }).click();
    await screen.getByRole("button", { name: "Name" }).click();
    const rows = screen.getByRole("row");
    await expect.element(rows.nth(1)).toHaveTextContent("Accounts by region");
    await expect.element(rows.nth(3)).toHaveTextContent("Sales overview");
    await screen.getByRole("button", { name: "Add Revenue trend to favorites" }).click();
    await screen.getByRole("button", { name: /^Favorites/ }).click();
    await expect.element(screen.getByText("Revenue trend")).toBeVisible();
    await expect.element(screen.getByText("Sales overview")).not.toBeInTheDocument();
    await screen.getByRole("button", { name: "Revenue trend", exact: true }).click();
    expect(onOpen).toHaveBeenCalledWith("c1", { edit: false });
  });

  it("creates a sheet from a query and opens it for editing", async () => {
    mocks.createArtifact.mockResolvedValue({ ...ARTIFACTS[0], artifact_id: "new-1" });
    const { screen, onOpen } = await mount();
    await screen.getByRole("button", { name: "New" }).click();
    await screen.getByRole("menuitem", { name: "Sheet from a query" }).click();
    await screen.getByRole("textbox", { name: "Name" }).fill("Orders");
    await screen.getByRole("textbox", { name: "Query" }).fill("SELECT * FROM sales.orders");
    await screen.getByRole("button", { name: "Create" }).click();
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalledWith("new-1", { edit: true }));
    expect(mocks.createArtifact).toHaveBeenCalledWith({
      title: "Orders",
      artifact_type: "chart",
      sql_text: "SELECT * FROM sales.orders",
    });
  });
});
