import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AgentMessage } from "@/features/agents/api";
import type { SemanticRef } from "@/features/intelligence/lifecycle-api";
import { api } from "@/lib/api-client";
import type { EvidenceEnvelope, EvidenceHealth } from "./evidence-health";
import { applyEvent, replayThread, type TranscriptTurn } from "./studio-transcript";
import { workflowApi, type Mission } from "./workflow-api";
import { WorkflowRail } from "./workflow-rail";
import "@/styles/index.css";

const missionA: Mission = {
  mission_id: "revenue", thread_id: "scope-thread", objective: "Revenue decline", work_intent: "INVESTIGATE",
  status: "completed", revision: 3, run_ids: ["run-revenue"], stages: [], evidence_refs: [], object_refs: [],
  cancel_requested: false, created_at: "2026-10-04T00:00:00Z", updated_at: "2026-10-04T00:00:00Z",
};
const missionB: Mission = { ...missionA, mission_id: "singapore", objective: "Singapore expansion", run_ids: ["run-singapore"] };
const health: EvidenceHealth = {
  schema_version: 1, rule_version: "evidence-health-v1", assessed_at: "2026-10-04T00:00:00Z", label: "limited",
  facts: {
    semantic_grounding: "published", semantic_view_id: "sales", semantic_version: 2, semantic_fingerprint: "sales-v2",
    plan_source: "compiled", verified_query_hit: false, verified_query_id: null, execution_status: "success",
    coverage: "partial", semantic_ambiguity: "none", source_agreement: "unknown", causal_strength: "association",
    unsupported_numeric_claims: false, data_as_of: null, max_age_seconds: null, evidence_refs: ["e1"],
  },
  data_freshness: { status: "unknown", data_as_of: null, age_seconds: null, max_age_seconds: null },
  reasons: ["coverage_partial"], unknown_signals: ["data_freshness"],
};
const semanticA = { view_id: "sales", version: 2, fingerprint: "sales-v2" };
const semanticB = { view_id: "sales", version: 3, fingerprint: "sales-v3" };
function envelope(semantic: SemanticRef, metric: string): EvidenceEnvelope {
  return {
    schema_version: 1, health, semantic, metrics: [metric], dimensions: [], current_window: null, baseline_window: null,
    timezone: "Asia/Jakarta", filter_shape: [], named_filters: [], warnings: [], evidence_refs: ["e1"],
    validated_plan_fingerprint: "a".repeat(64), model_fingerprint: semantic.fingerprint,
  };
}
const records = [
  { workflow: { mission_id: missionA.mission_id, run_id: "run-revenue", root_run_id: "root" }, envelope: envelope(semanticA, "revenue") },
  { workflow: { mission_id: missionB.mission_id, run_id: "run-singapore", root_run_id: "root" }, envelope: envelope(semanticB, "expansion_cost") },
  { workflow: undefined, envelope: envelope(semanticA, "legacy_margin") },
];
const empty: TranscriptTurn = {
  id: "question", question: "Revenue", steps: [], answer: "", content: [], pendingConsent: null,
  blocks: { tables: [], charts: [], citations: [] }, state: "streaming",
};
function transcript(saved: boolean) {
  if (saved) return replayThread([
    { message_id: "question", role: "user", content: "Revenue", created_at: "2026-10-04T00:00:00Z" },
    { message_id: "answer", role: "assistant", content: "Recorded", created_at: "2026-10-04T00:00:00Z", steps: records.map((record) => ({
      kind: "tool", name: "semantic_query", tool_call_id: "shared-call", status: "done", arguments: {},
      workflow: record.workflow, trace_detail: { evidence_envelope: record.envelope },
    })) },
  ] as AgentMessage[]);
  return records.reduce((turns, record) => applyEvent(turns, "question", {
    type: "evidence_envelope", tool_call_id: "shared-call", tool_name: "semantic_query", payload: record.envelope,
    workflow: record.workflow,
  }), [empty]);
}
function resolved(semantic: SemanticRef, term: string) {
  const node = { id: `${term}-${semantic.version}`, name: `${term} context v${semantic.version}`, kind: "metric", reference_id: "metric",
    state: "VERIFIED", semantic, source_kind: "published_semantic", authority: "published_semantic_definition", validity: "current" };
  return { status: "resolved", selected_metric: term, node_id: node.id, selected_node: node, semantic,
    graph: { nodes: [node], edges: [], bounded: true, conflicts: [] } };
}
function contrast(foreground: string, background: string) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d")!;
  const luminance = (color: string) => {
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = color;
    context.fillRect(0, 0, 1, 1);
    const values = [...context.getImageData(0, 0, 1, 1).data].slice(0, 3).map((value) => {
      const channel = value / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return values.reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  };
  const left = luminance(foreground), right = luminance(background);
  return (Math.max(left, right) + 0.05) / (Math.min(left, right) + 0.05);
}
let client: QueryClient;
function Host({ turns }: { turns: TranscriptTurn[] }) {
  const [available, setAvailable] = useState(false);
  const [open, setOpen] = useState(false);
  return <div className="flex h-dvh min-w-0 overflow-hidden">
    <main className="min-w-0 flex-1 p-3">Conversation
      {available && <button className="min-h-11" onClick={() => setOpen(true)}>Open workflow details</button>}
    </main>
    <WorkflowRail threadId="scope-thread" turns={turns} streaming={false} agents={[]} runs={[]} runLoading={false} runError={false}
      retryRuns={vi.fn()} onSelectChild={vi.fn()} onAvailable={setAvailable} mobileOpen={open} onMobileOpenChange={setOpen} />
  </div>;
}
async function host(turns: TranscriptTurn[]) {
  await render(<QueryClientProvider client={client}><Host turns={turns} /></QueryClientProvider>);
  await expect.element(page.getByRole("button", { name: "Open workflow details" })).toBeVisible();
  if (window.innerWidth < 1200) await page.getByRole("button", { name: "Open workflow details" }).click();
}
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  vi.spyOn(workflowApi, "list").mockResolvedValue({ missions: [missionA, missionB] });
  vi.spyOn(workflowApi, "deliverables").mockResolvedValue({ deliverables: [] });
});
afterEach(async () => {
  await cleanup();
  client.clear();
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
  await page.viewport(1280, 720);
});

describe("Mission evidence and Context scopes", () => {
  for (const saved of [false, true]) for (const dark of [false, true]) for (const width of [320, 1280]) {
    it(`keeps both Missions and explicit conversation evidence separate (saved=${saved}, dark=${dark}, width=${width})`, async () => {
      await page.viewport(width, 720);
      document.documentElement.classList.toggle("dark", dark);
      const post = vi.spyOn(api, "post").mockImplementation(async (_url, body) => {
        const { semantic, term } = body as { semantic: SemanticRef; term: string };
        return resolved(semantic, term) as never;
      });
      await host(transcript(saved));
      const selector = page.getByRole("combobox", { name: "Workflow mission" });
      await expect.element(selector).toHaveValue("mission:revenue");
      const style = getComputedStyle(selector.element());
      expect(contrast(style.color, style.backgroundColor)).toBeGreaterThanOrEqual(4.5);
      expect(selector.element().getBoundingClientRect().height).toBeGreaterThanOrEqual(44);
      await page.getByRole("tab", { name: "Evidence", exact: true }).click();
      await expect.element(page.getByText("Metrics: revenue", { exact: true })).toBeVisible();
      await expect.element(page.getByText("Metrics: expansion_cost", { exact: true })).not.toBeInTheDocument();
      await expect.element(page.getByText("Metrics: legacy_margin", { exact: true })).not.toBeInTheDocument();
      await page.getByRole("button", { name: "View revenue context", exact: true }).click();
      await expect.element(page.getByRole("button", { name: "revenue context v2", exact: true })).toBeVisible();
      expect(post).toHaveBeenLastCalledWith("/intelligence/context/resolve-metric", { semantic: semanticA, term: "revenue", exact: true, include_context: true });
      await page.getByLabelText("Find a business concept").fill("previous search");
      await selector.selectOptions("mission:singapore");
      await expect.element(page.getByRole("tab", { name: "Context", exact: true })).toHaveAttribute("aria-selected", "true");
      await expect.element(page.getByRole("button", { name: "expansion_cost context v3", exact: true })).toBeVisible();
      await expect.element(page.getByLabelText("Find a business concept")).toHaveValue("");
      await expect.element(page.getByRole("button", { name: "revenue context v2", exact: true })).not.toBeInTheDocument();
      expect(post).toHaveBeenLastCalledWith("/intelligence/context/resolve-metric", { semantic: semanticB, term: "expansion_cost", exact: true, include_context: true });
      await page.getByRole("tab", { name: "Evidence", exact: true }).click();
      await expect.element(page.getByText("Metrics: expansion_cost", { exact: true })).toBeVisible();
      await expect.element(page.getByText("Metrics: revenue", { exact: true })).not.toBeInTheDocument();
      await selector.selectOptions("conversation");
      for (const metric of ["revenue", "expansion_cost", "legacy_margin"]) await expect.element(page.getByText(`Metrics: ${metric}`, { exact: true })).toBeVisible();
      await expect.element(page.getByText("Conversation evidence · Mission provenance was not recorded", { exact: true })).toBeVisible();
      await selector.selectOptions("mission:revenue");
      await expect.element(page.getByText("Metrics: revenue", { exact: true })).toBeVisible();
      await expect.element(page.getByText("Metrics: expansion_cost", { exact: true })).not.toBeInTheDocument();
      await page.getByRole("tab", { name: "Activity", exact: true }).click();
      await expect.element(page.getByRole("heading", { name: missionA.objective, exact: true })).toBeVisible();
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
    });
  }
  it("discards a late exact Context result after changing Mission", async () => {
    let release: (value: unknown) => void = () => {};
    const post = vi.spyOn(api, "post").mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }))
      .mockResolvedValue(resolved(semanticB, "expansion_cost") as never);
    await host(transcript(false));
    await page.getByRole("tab", { name: "Context", exact: true }).click();
    await expect.poll(() => post.mock.calls.length).toBe(1);
    await page.getByRole("combobox", { name: "Workflow mission" }).selectOptions("mission:singapore");
    await expect.element(page.getByRole("button", { name: "expansion_cost context v3", exact: true })).toBeVisible();
    release(resolved(semanticA, "revenue"));
    await vi.waitFor(() => expect(client.isFetching()).toBe(0));
    await expect.element(page.getByRole("button", { name: "revenue context v2", exact: true })).not.toBeInTheDocument();
    await expect.element(page.getByRole("button", { name: "expansion_cost context v3", exact: true })).toBeVisible();
  });
  it("retains an explicitly chosen metric only when its exact ref is present in the next Mission", async () => {
    const turns = transcript(false);
    turns[0].evidence![0].envelope = { ...envelope(semanticA, "revenue"), metrics: ["revenue", "margin"] };
    turns[0].evidence![1].envelope = { ...envelope(semanticA, "expansion_cost"), metrics: ["expansion_cost", "margin"] };
    const post = vi.spyOn(api, "post").mockImplementation(async (_url, body) => {
      const { semantic, term } = body as { semantic: SemanticRef; term: string };
      return resolved(semantic, term) as never;
    });
    await host(turns);
    await page.getByRole("tab", { name: "Evidence", exact: true }).click();
    await page.getByRole("button", { name: "View margin context", exact: true }).click();
    await expect.element(page.getByRole("button", { name: "margin context v2", exact: true })).toBeVisible();
    const selector = page.getByRole("combobox", { name: "Workflow mission" });
    selector.element().focus();
    await expect.element(selector).toHaveFocus();
    await selector.selectOptions("mission:singapore");
    await expect.element(selector).toHaveValue("mission:singapore");
    await expect.element(selector).toHaveFocus();
    await userEvent.keyboard("{Tab}");
    await expect.element(page.getByRole("tab", { name: "Context", exact: true })).toHaveFocus();
    await expect.element(page.getByRole("button", { name: "margin context v2", exact: true })).toBeVisible();
    expect(post.mock.calls.every(([, body]) => (body as { term: string }).term === "margin")).toBe(true);
    await expect.element(page.getByRole("button", { name: "expansion_cost context v2", exact: true })).not.toBeInTheDocument();
  });
  it.each([
    { ...semanticA, version: 3 },
    { ...semanticA, fingerprint: "different-fingerprint" },
  ])("resets selected Context for a changed exact semantic identity (%j)", async (nextSemantic) => {
    const turns = transcript(false);
    turns[0].evidence![1].envelope = envelope(nextSemantic, "revenue");
    const post = vi.spyOn(api, "post").mockImplementation(async (_url, body) => {
      const { semantic, term } = body as { semantic: SemanticRef; term: string };
      return resolved(semantic, term) as never;
    });
    await host(turns);
    await page.getByRole("tab", { name: "Evidence", exact: true }).click();
    await page.getByRole("button", { name: "View revenue context", exact: true }).click();
    await expect.element(page.getByRole("button", { name: "revenue context v2", exact: true })).toBeVisible();
    await page.getByRole("combobox", { name: "Workflow mission" }).selectOptions("mission:singapore");
    await expect.poll(() => post.mock.calls.length).toBe(2);
    expect(post).toHaveBeenLastCalledWith("/intelligence/context/resolve-metric", { semantic: nextSemantic, term: "revenue", exact: true, include_context: true });
    await expect.element(page.getByLabelText("Find a business concept")).toHaveValue("");
  });
  it("keeps ANSWER evidence available without starting a Mission", async () => {
    vi.mocked(workflowApi.list).mockResolvedValue({ missions: [] });
    const create = vi.spyOn(workflowApi, "create");
    await host(transcript(true));
    await expect.element(page.getByText("This conversation has no mission")).toBeVisible();
    await page.getByRole("tab", { name: "Evidence", exact: true }).click();
    await expect.element(page.getByRole("combobox", { name: "Workflow mission" })).toHaveValue("conversation");
    await expect.element(page.getByText("Metrics: legacy_margin", { exact: true })).toBeVisible();
    expect(create).not.toHaveBeenCalled();
  });
});
