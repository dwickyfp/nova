import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { page } from "vitest/browser";
import { afterEach, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { sharingApi } from "@/features/agents/studio-intelligence-api";
import { StudioShared } from "./studio-shared";
import "@/styles/index.css";

afterEach(async () => {
  vi.restoreAllMocks();
  await page.viewport(1440, 900);
});

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <div className="flex h-[700px] min-h-0 flex-col bg-background"><StudioShared /></div>
    </QueryClientProvider>,
  );
}

it("opens a shared dashboard and runs each tile with the viewer's access", async () => {
  vi.spyOn(sharingApi, "sharedWithMe").mockResolvedValue({
    count: 1,
    shared: [{ share_id: "s1", object_type: "dashboard", object_id: "d1", owner_name: "alice",
      target_type: "role", target_name: "SALES_ANALYST" }],
  });
  vi.spyOn(sharingApi, "openDashboard").mockResolvedValue({
    dashboard_id: "d1", title: "Weekly revenue", owner_name: "alice",
    layout: { tiles: [
      { tile_id: "t2", artifact_id: "a2", x: 0, y: 4, w: 6, h: 4 },
      { tile_id: "t1", artifact_id: "a1", x: 0, y: 0, w: 6, h: 4 },
    ] },
  });
  vi.spyOn(sharingApi, "refreshTile").mockImplementation(async (_dashboard, artifactId) => {
    if (artifactId === "a2") throw new Error("Your access does not cover this tile");
    return {
      artifact: { artifact_id: "a1", title: "Revenue by city", artifact_type: "table", chart_spec: null },
      columns: ["city", "total_revenue"], rows: [["Medan", 1200]], row_count: 1,
    };
  });
  mount();
  await page.getByRole("button", { name: /Dashboard from alice/ }).click();
  await expect.element(page.getByRole("heading", { name: "Weekly revenue" })).toBeVisible();
  await expect.element(page.getByText("Medan")).toBeVisible();
  await expect.element(page.getByText("Your access does not cover this tile")).toBeVisible();
});

it("fits a phone width", async () => {
  await page.viewport(360, 740);
  vi.spyOn(sharingApi, "sharedWithMe").mockResolvedValue({
    count: 1,
    shared: [{ share_id: "s1", object_type: "thread", object_id: "t1", owner_name: "a".repeat(80),
      target_type: "user", target_name: "bob" }],
  });
  mount();
  await expect.element(page.getByRole("button", { name: /Conversation from/ })).toBeVisible();
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(360);
});
