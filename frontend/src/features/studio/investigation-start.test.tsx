import { afterEach, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { intelligenceApi } from "@/features/intelligence/lifecycle-api";
import { semanticViewsApi } from "@/features/intelligence/semantic-views-api";
import { InvestigationStart } from "./investigation-start";
import { workflowApi, type Mission } from "./workflow-api";
import type { EvidenceEnvelope } from "./evidence-health";

const semantic = { view_id: "sales", version: 2, fingerprint: "published-two" };
const established: EvidenceEnvelope = {
  schema_version: 1,
  health: {
    schema_version: 1, rule_version: "evidence-health-v1", assessed_at: "2026-03-03T00:00:00Z",
    label: "limited", facts: {
      semantic_grounding: "published", semantic_view_id: semantic.view_id, semantic_version: 2,
      semantic_fingerprint: semantic.fingerprint, plan_source: "compiled", verified_query_hit: false,
      verified_query_id: null, execution_status: "success", coverage: "complete",
      semantic_ambiguity: "none", source_agreement: "unknown", causal_strength: "unknown",
      unsupported_numeric_claims: false, data_as_of: null, max_age_seconds: null, evidence_refs: [],
    },
    data_freshness: { status: "unknown", data_as_of: null, age_seconds: null, max_age_seconds: null },
    reasons: ["freshness_unknown"], unknown_signals: ["data_freshness"],
  },
  semantic, metrics: ["revenue"], dimensions: ["orders.region"],
  current_window: { start: "2026-03-01T00:00:00.000Z", end: "2026-03-03T00:00:00.123Z" },
  baseline_window: { start: "2026-02-01T00:00:00.000Z", end: "2026-02-03T00:00:00.123Z" },
  timezone: "Asia/Jakarta", filter_shape: [], named_filters: [],
  warnings: [], evidence_refs: [], validated_plan_fingerprint: "a".repeat(64), model_fingerprint: "model",
};
const mission: Mission = {
  mission_id: "mission", thread_id: "thread", agent_id: "finance", objective: "Revenue",
  work_intent: "INVESTIGATE", status: "blocked", revision: 3, run_ids: [], stages: [],
  evidence_refs: [], object_refs: [], cancel_requested: false, created_at: "2026-03-03", updated_at: "2026-03-03",
  investigation_requirements: { required_inputs: ["comparison_windows"], established },
};
function mount(value: Mission = mission) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><InvestigationStart mission={value} refresh={vi.fn()} /></QueryClientProvider>);
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function sources() {
  vi.spyOn(intelligenceApi, "page").mockResolvedValue({ items: [], next_after: null });
  vi.spyOn(semanticViewsApi, "get").mockResolvedValue({ versions: [{ version: 2, fingerprint: semantic.fingerprint,
    definition: { metrics: [{ name: "revenue", default_time_dimension: "orders.ordered_at" }],
      datasets: [{ name: "orders", fields: [{ name: "ordered_at", dimension: { is_time: true } }] }] },
  }] } as Awaited<ReturnType<typeof semanticViewsApi.get>>);
}
it("prefills fixed windows and creates an honest disabled one-time comparison", async () => {
  sources();
  const post = vi.spyOn(api, "post").mockResolvedValue({ status: "created", investigation: { id: "inv", revision: 1 } });
  vi.spyOn(workflowApi, "get").mockResolvedValue(mission);
  const link = vi.spyOn(workflowApi, "link").mockResolvedValue(mission);
  mount();
  await page.getByRole("button", { name: "Complete investigation setup" }).click();
  await expect.element(page.getByText("Sample count remains unknown for this one-time comparison.")).toBeVisible();
  await expect.element(page.getByRole("button", { name: "Run governed investigation" })).toBeEnabled();
  await page.getByRole("button", { name: "Run governed investigation" }).click();
  await vi.waitFor(() => expect(link).toHaveBeenCalled());
  expect(post).toHaveBeenCalledWith("/intelligence/investigations/from-chat", expect.objectContaining({
    configuration: expect.objectContaining({ semantic, agent_id: "finance", count_column: null, enabled: false }),
    current_window: established.current_window, baseline_window: established.baseline_window,
    calendar_timezone: "Asia/Jakarta",
  }));
});
it("keeps incomplete filtered populations blocked when no exact governed comparison is selected", async () => {
  sources();
  mount({ ...mission, investigation_requirements: { required_inputs: ["comparison_windows"],
    established: { ...established, filter_shape: [{ field: "orders.region", operator: "=" }] } } });
  await page.getByRole("button", { name: "Complete investigation setup" }).click();
  await expect.element(page.getByText("Sample count remains unknown for this one-time comparison.")).toBeVisible();
  await expect.element(page.getByRole("button", { name: "Run governed investigation" })).toBeDisabled();
});
