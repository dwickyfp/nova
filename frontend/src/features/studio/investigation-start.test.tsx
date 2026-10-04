import { afterEach, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { intelligenceApi } from "@/features/intelligence/lifecycle-api";
import { InvestigationStart } from "./investigation-start";
import { workflowApi, type Mission } from "./workflow-api";

afterEach(async () => { await cleanup(); vi.restoreAllMocks(); });

it("uses a disabled one-off comparison, preserves explicit windows, and reports insufficient evidence", async () => {
  vi.spyOn(intelligenceApi, "page").mockResolvedValue({ items: [{ id: "monitor", revision: 2, scope: { principal: "private" }, name: "Revenue", agent_id: "analyst", semantic: { view_id: "sales", version: 1, fingerprint: "fp" }, plan: { metrics: ["revenue"] }, value_column: "revenue", count_column: "count", time_dimension: "day", enabled: true }], next_after: null } as never);
  const post = vi.spyOn(api, "post").mockResolvedValue({ status: "insufficient", reason: "missing_observations", investigation: null });
  const link = vi.spyOn(workflowApi, "link");
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><InvestigationStart mission={{ mission_id: "m1", thread_id: "t1" } as Mission} refresh={vi.fn()} /></QueryClientProvider>);
  await page.getByRole("button", { name: "Investigate a metric comparison", exact: true }).click();
  await expect.element(page.getByRole("button", { name: "Run governed investigation" })).toBeDisabled();
  await page.getByLabelText("Metric comparison", { exact: true }).selectOptions("monitor");
  await page.getByLabelText("Baseline start", { exact: true }).fill("2026-10-01T00:00");
  await page.getByLabelText("Baseline end", { exact: true }).fill("2026-10-02T00:00");
  await page.getByLabelText("Observed start", { exact: true }).fill("2026-10-02T00:00");
  await page.getByLabelText("Observed end", { exact: true }).fill("2026-10-03T00:00");
  await page.getByRole("button", { name: "Run governed investigation" }).click();
  await expect.element(page.getByText("Evidence is incomplete: missing observations.")).toBeVisible();
  const body = post.mock.calls[0][1] as { configuration: { enabled: boolean; scope?: unknown }; baseline_window: { start: string; end: string }; current_window: { start: string; end: string } };
  expect(post.mock.calls[0][0]).toBe("/intelligence/investigations/from-chat");
  expect(body.configuration.enabled).toBe(false);
  expect(body.configuration.scope).toBeUndefined();
  expect(new Date(body.baseline_window.end).getTime() - new Date(body.baseline_window.start).getTime()).toBe(86400000);
  expect(body.baseline_window.end).toBe(body.current_window.start);
  expect(link).not.toHaveBeenCalled();
});
