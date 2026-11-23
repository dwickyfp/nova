import { afterEach, describe, expect, it } from "vitest";
import type { AgentMessage } from "@/features/agents/api";
import { evidenceIdentity, mergeToolEvidence, readEvidenceHealth, type EvidenceEnvelope, type EvidenceHealth } from "./evidence-health";
import type { AssistantEvent } from "@/features/assistant/types";
import { applyStudioEvent, newTurn, replayThread, type TranscriptTurn } from "./studio-transcript";
import { liveEncoder } from "./runtime/v2-test-encoder";
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
/** A live Studio turn fed loop events through the protocol v2 encoding. */
function live() {
  const feed = liveEncoder({ runId: "route-run" });
  let turns: TranscriptTurn[] = [newTurn({ id: "u1", question: "Revenue" })];
  return (...events: AssistantEvent[]) => {
    for (const event of events) for (const v2 of feed(event)) turns = applyStudioEvent(turns, "u1", v2);
    return turns;
  };
}
const call = (tool_call_id: string, run_id?: string, sql_preview = ""): AssistantEvent => ({
  type: "tool_call",
  ...(run_id ? { run_id } : {}),
  payload: { tool_call_id, tool_name: "semantic_query", sql_preview, classification: "read_only", status: "done" },
});

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
    const push = live();
    let live_: TranscriptTurn[] = [];
    for (const [mission_id, run_id] of [["a", "run-a"], ["b", "run-b"]]) {
      live_ = push(
        call("same", run_id, `SELECT '${mission_id}'`),
        { type: "evidence_envelope", tool_call_id: "same", tool_name: "semantic_query", payload: provenance, run_id, workflow: workflow(mission_id, run_id) },
        { type: "evidence_health", tool_call_id: "same", payload: assessment, run_id, workflow: workflow(mission_id, run_id) },
      );
    }
    const liveTurns = live_;
    expect(liveTurns[0].evidence).toHaveLength(2);
    expect(new Set(liveTurns[0].evidence?.map(evidenceIdentity)).size).toBe(2);
    expect(liveTurns[0].evidence?.map((item) => item.sqlPreview)).toEqual(["SELECT 'a'", "SELECT 'b'"]);
    expect(liveTurns[0].evidence?.every((item) => item.envelope?.semantic?.view_id === "sales")).toBe(true);
    const steps = ["a", "b"].map((mission_id) => ({ kind: "tool", name: "semantic_query", tool_call_id: "same", status: "done", arguments: {}, preview: `SELECT '${mission_id}'`, workflow: workflow(mission_id, `run-${mission_id}`), trace_detail: { evidence_health: assessment, evidence_envelope: provenance } }));
    const saved = replayThread([{ message_id: "u1", role: "user", content: "Revenue", created_at: "2026-10-03T09:00:00Z" }, { message_id: "answer", role: "assistant", content: "Recorded", created_at: "2026-10-03T10:00:00Z", steps }] as AgentMessage[]);
    expect(saved[0].evidence).toEqual(liveTurns[0].evidence);
  });
  it("reads nested trace provenance and never infers Mission membership for legacy evidence", () => {
    const workflow = { mission_id: "a", run_id: "run-a", root_run_id: null };
    const saved = replayThread([{ message_id: "u1", role: "user", content: "Revenue", created_at: "2026-10-03T09:00:00Z" }, { message_id: "answer", role: "assistant", content: "Recorded", created_at: "2026-10-03T10:00:00Z", steps: [{ kind: "tool", name: "semantic_query", tool_call_id: "c1", status: "done", arguments: {}, trace_detail: { evidence_envelope: provenance, workflow } }] }] as AgentMessage[]);
    expect(saved[0].evidence?.[0].workflowProvenance).toEqual(workflow);
    const legacy = live()(call("legacy", "run-a"), { type: "evidence_health", tool_call_id: "legacy", run_id: "run-a", payload: assessment });
    expect(legacy[0].evidence?.[0].workflowProvenance).toBeUndefined();
    expect(legacy[0].evidence?.[0].runId).toBe("run-a");
  });
  it("reconstructs exactly the live bounded provenance from persisted evidence", () => {
    const liveTurns = live()(call("c1"), { type: "evidence_envelope", tool_call_id: "c1", tool_name: "semantic_query", payload: provenance });
    const saved = replayThread([{ message_id: "u1", role: "user", content: "Revenue", created_at: "2026-10-03T09:00:00Z" },
      { message_id: "a1", role: "assistant", content: "Revenue changed", created_at: "2026-10-03T10:00:00Z", steps: [{ kind: "tool", name: "semantic_query", tool_call_id: "c1", status: "done", arguments: {}, trace_detail: { evidence_health: assessment, evidence_envelope: provenance } }] }] as AgentMessage[]);
    expect(saved[0].evidence).toEqual(liveTurns[0].evidence);
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
    const push = live();
    push(call("c1"), event);
    const twice = push(event);
    expect(twice[0].evidence).toHaveLength(1);
    const cleared = push({ type: "role_changed", active_role: "ANALYST", security_context_version: 2 });
    expect(cleared[0].evidence).toEqual([]);
  });
  it("does not invent assessments for legacy results or malformed evidence", () => {
    const result = live()(call("c1"), { type: "evidence_health", tool_call_id: "c1", payload: { label: "strong" } as never });
    expect(result[0].evidence).toBeUndefined();
  });
});
