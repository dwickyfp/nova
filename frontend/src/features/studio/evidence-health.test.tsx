import { afterEach, describe, expect, it } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import type { AgentMessage } from "@/features/agents/api";
import { EvidencePanel } from "./evidence-panel";
import { readEvidenceHealth, type EvidenceHealth } from "./evidence-health";
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

afterEach(() => document.documentElement.classList.remove("dark"));

describe("Evidence Health replay", () => {
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
