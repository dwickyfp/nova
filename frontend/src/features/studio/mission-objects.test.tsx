import { afterEach, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api-client";
import { intelligenceApi, type Decision, type Investigation, type News, type MonitorConfiguration } from "@/features/intelligence/lifecycle-api";
import { MissionObjects } from "./mission-objects";
import { workflowApi, type Mission } from "./workflow-api";
import "@/styles/index.css";

const mission: Mission = {
  mission_id: "m1", thread_id: "t1", objective: "Revenue investigation", work_intent: "INVESTIGATE",
  status: "blocked", revision: 3, run_ids: [], stages: [], evidence_refs: ["q1"],
  object_refs: [{ kind: "investigation", id: "inv", revision: 2 }], cancel_requested: false,
  created_at: "2026-10-04T00:00:00Z", updated_at: "2026-10-04T00:00:00Z",
};
const investigation: Investigation = {
  id: "inv", revision: 2, status: "complete", news_id: "news", evidence: [], residual: 0,
  created_at: "2026-10-04T00:00:00Z", semantic: { view_id: "sales", version: 1, fingerprint: "sales-v1" },
  decompositions: [], timeline: [], hypotheses: [{ id: "hypothesis", label: "Regional arithmetic contribution", contribution: -10, causal_status: "arithmetic", next_test: "Inspect regional detail", evidence_ids: ["q1"] }],
};
const news: News = { id: "news", revision: 1, created_at: "2026-10-04T00:00:00Z", status: "detected", title: "Revenue declined", summary: "Recorded comparison", severity: "warning", before: 100, after: 90, change: -10, monitor_id: "monitor", evidence: [], semantic: investigation.semantic, window: { start: "2026-10-01T00:00:00Z", end: "2026-10-04T00:00:00Z" }, confidence: { label: "medium", dimension: "statistical" } };
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
afterEach(async () => { await cleanup(); vi.restoreAllMocks(); await page.viewport(1280, 720); });

it("shows the canonical hypothesis immediately and composes with the exact pinned News", async () => {
  await page.viewport(320, 720);
  const canonical = vi.spyOn(workflowApi, "canonical").mockResolvedValue(investigation);
  const general = vi.spyOn(intelligenceApi, "get").mockRejectedValue(new Error("General read must not supply historical evidence"));
  const context = vi.spyOn(workflowApi, "investigationContext").mockResolvedValue({ investigation, news, monitor: {} as MonitorConfiguration });
  const followup = vi.fn();
  render(<QueryClientProvider client={client()}><MissionObjects mission={mission} onFollowUp={followup} /></QueryClientProvider>);
  await expect.element(page.getByRole("heading", { name: "Regional arithmetic contribution" })).toBeVisible();
  expect(canonical.mock.calls[0][0]).toBe("m1");
  expect(canonical.mock.calls[0][1]).toEqual({ kind: "investigation", id: "inv", revision: 2 });
  await expect.element(page.getByText("Recorded revision 2", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Continue investigation", exact: true }).click();
  expect(followup.mock.calls[0][1]).toBe("m1");
  await page.getByRole("button", { name: "Compare scenarios and prepare decision", exact: true }).click();
  await expect.element(page.getByText("Prepare a decision", { exact: true })).toBeVisible();
  expect(context.mock.calls[0]).toEqual(["m1", "inv", 2]);
  expect(general).not.toHaveBeenCalled();
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
});

it("does not display a hypothesis when the exact revision is no longer authorized", async () => {
  vi.spyOn(workflowApi, "canonical").mockRejectedValue(new ApiError(403, "Permission denied"));
  render(<QueryClientProvider client={client()}><MissionObjects mission={mission} onFollowUp={vi.fn()} /></QueryClientProvider>);
  await expect.element(page.getByText("Record unavailable", { exact: true })).toBeVisible();
  await expect.element(page.getByText("Permission denied", { exact: true })).toBeVisible();
  await expect.element(page.getByRole("heading", { name: "Regional arithmetic contribution" })).not.toBeInTheDocument();
});

it("prepares either Action adapter from the exact Mission Investigation after resume", async () => {
  const decision: Decision = {
    id: "decision", revision: 3, mission_id: "m1", title: "Revenue response", status: "selected",
    created_at: "2026-10-04T00:00:00Z", semantic: investigation.semantic, evidence: [], currency: "USD",
    selected_option_id: "option", outcome_window: news.window,
    options: [{ id: "option", description: "Governed response", action_type: "price", assumptions: {}, prediction: 10, cost: 0, risk: "low", feasible: true, method: "scenario" }],
  };
  const resumed = { ...mission, object_refs: [{ kind: "decision" as const, id: decision.id, revision: 3 }] };
  vi.spyOn(workflowApi, "canonical").mockResolvedValue(decision);
  const general = vi.spyOn(intelligenceApi, "get").mockRejectedValue(new Error("Historical Monitor requires its Mission binding"));
  const lineage = vi.spyOn(intelligenceApi, "lineage").mockResolvedValue({ news, investigation, decision, evidence: [] });
  const policy = vi.spyOn(intelligenceApi, "policy").mockResolvedValue({ current: true, policy_revision: 1, can_edit: false, can_review: true });
  const monitor: MonitorConfiguration = { name: "Revenue comparison", agent_id: "finance", semantic: investigation.semantic, plan: {}, value_column: "revenue", time_dimension: "sales.date", timezone: "Asia/Jakarta" };
  const context = vi.spyOn(workflowApi, "investigationContext").mockResolvedValue({ investigation, news, monitor });
  render(<QueryClientProvider client={client()}><MissionObjects mission={resumed} onFollowUp={vi.fn()} /></QueryClientProvider>);
  await page.getByRole("button", { name: "Inspect decision", exact: true }).click();
  await page.getByRole("button", { name: "Prepare governed action", exact: true }).click();
  await expect.element(page.getByRole("radio", { name: "Scheduled Studio report" })).toBeVisible();
  expect(lineage).toHaveBeenCalledWith("decision", "m1");
  expect(context).toHaveBeenCalledWith("m1", "inv", 2);
  expect(policy).toHaveBeenCalledWith("decision", "m1");
  expect(general).not.toHaveBeenCalled();
  await page.getByRole("radio", { name: "Scheduled Studio report" }).click();
  await expect.element(page.getByRole("textbox", { name: "Report title" })).toBeVisible();
  await expect.element(page.getByRole("button", { name: "Preview automation action", exact: true })).toBeVisible();
});
