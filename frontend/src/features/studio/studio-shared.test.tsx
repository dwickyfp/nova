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
    viz_spec: { version: 1, title: "Weekly revenue", zones: [
      { zone_id: "z1", kind: "sheet", artifact_id: "a1",
        rect: { x: 0, y: 0, w: 12, h: 2 } },
      { zone_id: "z2", kind: "sheet", artifact_id: "a2",
        rect: { x: 12, y: 0, w: 12, h: 2 } },
    ] },
  });
  vi.spyOn(sharingApi, "computeDashboard").mockResolvedValue({
    title: "Weekly revenue",
    zones: [
      {
        zone_id: "z1",
        artifact_id: "a1",
        title: "Revenue by city",
        presmodel: {
          version: 1,
          title: "Revenue by city",
          mark: "bar",
          columns: [
            { name: "city", field: "city", field_type: "nominal", role: "dimension", aggregation: null, discrete: true, label: "city" },
            { name: "sum(total_revenue)", field: "total_revenue", field_type: "quantitative", role: "measure", aggregation: "sum", discrete: false, label: "sum(total_revenue)" },
          ],
          rows: [["Medan", 1200]],
          row_count: 1,
          axes: {
            x: { field: "city", label: "city", field_type: "nominal", discrete: true },
            y: { field: "sum(total_revenue)", label: "sum(total_revenue)", field_type: "quantitative", discrete: false },
          },
          legend: { color: null },
          totals: { "sum(total_revenue)": 1200 },
        },
        sql: "SELECT 1",
        row_count: 1,
      },
      { zone_id: "z2", artifact_id: "a2", error: "Your access does not cover this chart" },
    ],
  });
  mount();
  await page.getByRole("button", { name: /Dashboard from alice/ }).click();
  await expect.element(page.getByRole("heading", { name: "Weekly revenue" })).toBeVisible();
  await expect.element(page.getByText("Medan")).toBeVisible();
  await expect.element(page.getByText("Your access does not cover this chart")).toBeVisible();
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
