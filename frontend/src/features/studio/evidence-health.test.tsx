import { afterEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import type { AgentMessage } from "@/features/agents/api";
import { EvidencePanel } from "./evidence-panel";
import { evidenceIdentity, mergeToolEvidence, readEvidenceHealth, type EvidenceEnvelope, type EvidenceHealth } from "./evidence-health";
import { applyEvent, replayThread, type TranscriptTurn } from "./studio-transcript";
import "@/styles/index.css";

const assessment: EvidenceHealth = {
  schema_version: 1,
  rule_version: "evidence-health-v1",
  assessed_at: "2026-10-03T10:00:00Z",
  label: "limited",
  facts: {
    semantic_grounding: "published", semantic_view_id: "sales", semantic_version: 2,
    semantic_fingerprint: "fp", plan_source: "compiled", verified_query_hit: false,
    verified_query_id: null, execution_status: "success", coverage: "truncated",
    semantic_ambiguity: "none", source_agreement: "unknown", causal_strength: "association",
    unsupported_numeric_claims: false, data_as_of: null, max_age_seconds: null,
    evidence_refs: ["e1"],
  },
  data_freshness: { status: "unknown", data_as_of: null, age_seconds: null, max_age_seconds: null },
  reasons: ["coverage_truncated", "freshness_unknown"],
  unknown_signals: ["source_agreement", "data_freshness"],
};
const emptyTurn: TranscriptTurn = { id: "u1", question: "Revenue", steps: [], answer: "", content: [],
  pendingConsent: null, blocks: { tables: [], charts: [], citations: [] }, state: "streaming" };

const provenance: EvidenceEnvelope = {
  schema_version: 1, health: assessment,
  semantic: { view_id: "sales", version: 2, fingerprint: "fp" }, metrics: ["revenue"],
  dimensions: ["region"], current_window: { start: "2026-10-01T00:00:00Z", end: "2026-10-03T10:00:00Z" },
  baseline_window: { start: "2026-09-01T00:00:00Z", end: "2026-09-03T10:00:00Z" },
  timezone: "Asia/Jakarta", filter_shape: [{ field: "region", operator: "=" }], named_filters: [],
  warnings: [], evidence_refs: ["e1"], validated_plan_fingerprint: "a".repeat(64), model_fingerprint: "fp",
};

afterEach(() => document.documentElement.classList.remove("dark"));

describe("Evidence Health replay", () => {
  it("preserves recorded provenance and SQL when a later persisted assessment has no envelope", () => {
    const workflowProvenance = { mission_id: "a", run_id: "run-a", root_run_id: null };
    const previous = { toolCallId: "same", toolName: "semantic_query", health: assessment, envelope: provenance, workflowProvenance, runId: "run-a", sqlPreview: "SELECT revenue" };
    const next = { ...assessment, label: "moderate" as const };
    const merged = mergeToolEvidence([previous], { toolCallId: "same", toolName: "semantic_query", runId: "run-a", health: next, envelope: undefined, workflowProvenance: undefined, sqlPreview: undefined });
    expect(merged).toEqual([{ ...previous, health: next, envelope: { ...provenance, health: next } }]);
    expect(previous.envelope.health).toEqual(assessment);
  });
  it("keeps identical tool-call IDs from different runs and preserves envelopes after health updates", () => {
    const workflow = (mission_id: string, run_id: string) => ({ mission_id, run_id, root_run_id: "root" });
    let live = [emptyTurn];
    for (const [mission_id, run_id] of [["a", "run-a"], ["b", "run-b"]]) {
      live = applyEvent(live, "u1", { type: "tool_call", run_id, payload: { tool_call_id: "same", tool_name: "semantic_query", sql_preview: `SELECT '${mission_id}'`, classification: "read_only", status: "done" } });
      live = applyEvent(live, "u1", { type: "evidence_envelope", tool_call_id: "same", tool_name: "semantic_query", payload: provenance, run_id, workflow: workflow(mission_id, run_id) });
      live = applyEvent(live, "u1", { type: "evidence_health", tool_call_id: "same", payload: assessment, run_id, workflow: workflow(mission_id, run_id) });
    }
    expect(live[0].evidence).toHaveLength(2);
    expect(new Set(live[0].evidence?.map(evidenceIdentity)).size).toBe(2);
    expect(live[0].evidence?.map((item) => item.sqlPreview)).toEqual(["SELECT 'a'", "SELECT 'b'"]);
    expect(live[0].evidence?.every((item) => item.envelope?.semantic?.view_id === "sales")).toBe(true);
    const steps = ["a", "b"].map((mission_id) => ({ kind: "tool", name: "semantic_query", tool_call_id: "same", status: "done", arguments: {}, preview: `SELECT '${mission_id}'`, workflow: workflow(mission_id, `run-${mission_id}`), trace_detail: { evidence_health: assessment, evidence_envelope: provenance } }));
    const saved = replayThread([{ message_id: "u1", role: "user", content: "Revenue", created_at: "2026-10-03T09:00:00Z" }, { message_id: "answer", role: "assistant", content: "Recorded", created_at: "2026-10-03T10:00:00Z", steps }] as AgentMessage[]);
    expect(saved[0].evidence).toEqual(live[0].evidence);
  });
  it("reads nested trace provenance and never infers Mission membership for legacy evidence", () => {
    const workflow = { mission_id: "a", run_id: "run-a", root_run_id: null };
    const saved = replayThread([{ message_id: "u1", role: "user", content: "Revenue", created_at: "2026-10-03T09:00:00Z" }, { message_id: "answer", role: "assistant", content: "Recorded", created_at: "2026-10-03T10:00:00Z", steps: [{ kind: "tool", name: "semantic_query", tool_call_id: "c1", status: "done", arguments: {}, trace_detail: { evidence_envelope: provenance, workflow } }] }] as AgentMessage[]);
    expect(saved[0].evidence?.[0].workflowProvenance).toEqual(workflow);
    const legacy = applyEvent([emptyTurn], "u1", { type: "evidence_health", tool_call_id: "legacy", run_id: "run-a", payload: assessment });
    expect(legacy[0].evidence?.[0].workflowProvenance).toBeUndefined();
    expect(legacy[0].evidence?.[0].runId).toBe("run-a");
  });
  it("reconstructs exactly the live bounded provenance from persisted evidence", () => {
    const live = applyEvent([emptyTurn], "u1", { type: "evidence_envelope", tool_call_id: "c1", tool_name: "semantic_query", payload: provenance });
    const saved = replayThread([{ message_id: "u1", role: "user", content: "Revenue", created_at: "2026-10-03T09:00:00Z" },
      { message_id: "a1", role: "assistant", content: "Revenue changed", created_at: "2026-10-03T10:00:00Z", steps: [{ kind: "tool", name: "semantic_query", tool_call_id: "c1", status: "done", arguments: {}, trace_detail: { evidence_health: assessment, evidence_envelope: provenance } }] }] as AgentMessage[]);
    expect(saved[0].evidence).toEqual(live[0].evidence);
  });
  it("preserves the recorded assessment and excludes fields outside the public contract", () => {
    const health = readEvidenceHealth({ ...assessment, internal_config: "private" });
    expect(health).toEqual(assessment);
    expect(readEvidenceHealth({ ...assessment, label: "certain" })).toBeNull();
    expect(readEvidenceHealth({ ...assessment, rule_version: "unknown-rule" })).toBeNull();
  });
  it("restores the same assessment from a saved tool trace without consulting the clock", () => {
    const messages = [{ message_id: "u1", role: "user", content: "Revenue", created_at: "2026-10-03T09:00:00Z" },
      { message_id: "a1", role: "assistant", content: "Revenue changed", created_at: "2026-10-03T10:00:00Z",
        steps: [{ kind: "tool", name: "semantic_query", tool_call_id: "c1", status: "done", arguments: {},
          trace_detail: { evidence_health: assessment } }] }] as AgentMessage[];
    expect(replayThread(messages)[0].evidence?.[0].health).toEqual(assessment);
  });
  it("deduplicates live assessments by tool call and clears them at a role change", () => {
    const event = { type: "evidence_health" as const, tool_call_id: "c1", tool_name: "semantic_query", payload: assessment };
    const once = applyEvent([emptyTurn], "u1", event);
    const twice = applyEvent(once, "u1", event);
    expect(twice[0].evidence).toHaveLength(1);
    const cleared = applyEvent(twice, "u1", { type: "role_changed", active_role: "ANALYST", security_context_version: 2 });
    expect(cleared[0].evidence).toEqual([]);
  });
  it("does not invent assessments for legacy results or malformed evidence", () => {
    const result = applyEvent([emptyTurn], "u1", { type: "evidence_health", tool_call_id: "c1", payload: { label: "strong" } });
    expect(result[0].evidence).toBeUndefined();
  });
});

describe("Evidence surface", () => {
  it("selects exact semantic identity and metric for Context without displaying filter literals", async () => {
    const select = vi.fn();
    render(<EvidencePanel evidence={[{ toolCallId: "c1", toolName: "semantic_query", health: assessment, envelope: provenance }]} onSelectContext={select} />);
    await page.getByRole("button", { name: "View revenue context" }).click();
    expect(select).toHaveBeenCalledWith(provenance.semantic, "revenue");
    await expect.element(page.getByText("Filters: region =")).toBeVisible();
  });
  it("distinguishes absent evidence from a failing assessment", async () => {
    render(<EvidencePanel evidence={[]} />);
    await expect.element(page.getByText("Evidence has not been assessed")).toBeVisible();
    await expect.element(page.getByText("Insufficient evidence", { exact: true })).not.toBeInTheDocument();
  });
  for (const dark of [false, true]) it(`shows limitations and separate causality with keyboard SQL disclosure (dark: ${dark})`, async () => {
    await page.viewport(320, 700);
    document.documentElement.classList.toggle("dark", dark);
    render(<div className="w-full max-w-full p-3"><EvidencePanel
      evidence={[{ toolCallId: "c1", toolName: "semantic_query", health: assessment }]}
      sqlPreviews={{ c1: "SELECT SUM(revenue) FROM sales" }}
    /></div>);
    await expect.element(page.getByText("Limited evidence")).toBeVisible();
    await expect.element(page.getByText("Association", { exact: true })).toBeVisible();
    await expect.element(page.getByText("coverage truncated")).toBeVisible();
    const disclosure = page.getByText("SQL details", { exact: true });
    disclosure.element().focus();
    await page.getByText("SQL details", { exact: true }).click();
    await expect.element(page.getByText("SELECT SUM(revenue) FROM sales")).toBeVisible();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
  });
});
