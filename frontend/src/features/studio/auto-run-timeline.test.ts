import { afterEach, expect, it, vi } from "vitest";
import { agentsApi, type Agent, type AutoRunEvent, type AutoThreadRun } from "@/features/agents/api";
import { autoRunSteps, loadAutoRunEvents, rootRunForTurn } from "./auto-run-timeline";

const event = (event_id: number, run_id: string, type: string, payload: Record<string, unknown> = {}): AutoRunEvent => ({
  event_id, run_id, type, payload, created_at: "2026-09-25T00:00:00",
});

afterEach(() => vi.restoreAllMocks());

it("does not claim a specialist was involved when only the root ran tools", () => {
  const rows = autoRunSteps([
    event(1, "root", "agent_started"),
    event(2, "root", "child_activity", { event_type: "thinking", text: "Access verified" }),
    event(3, "root", "tool_activity", { tool_name: "query_execute" }),
    event(4, "root", "child_activity", { event_type: "tool_call", tool_name: "query_execute" }),
    event(5, "root", "agent_completed"),
  ], "root", []);
  expect(rows.some((row) => row.kind === "delegate")).toBe(false);
  expect(rows.filter((row) => row.kind === "tool").map((row) => row.label)).toEqual(["query_execute"]);
});

it("replays actual delegation, messages, waiting, and completion without a stale active step", () => {
  const events = [
    event(1, "root", "agent_started"),
    event(2, "root", "delegation_plan", { summary: "Use Sales Agent for channel metrics" }),
    event(3, "sales", "agent_queued", { agent_id: "sales-agent", objective: "Compare channels" }),
    event(4, "sales", "agent_started"),
    event(5, "root", "agent_waiting"),
    event(6, "root", "agent_message", { sender_run_id: "root", recipient_run_id: "sales", content: "Include gross margin" }),
    event(7, "sales", "tool_activity", { tool_name: "semantic_query" }),
    event(8, "sales", "child_activity", { event_type: "tool_call", tool_name: "semantic_query" }),
    event(9, "sales", "agent_message", { sender_run_id: "sales", recipient_run_id: "root", content: "Five rows ready" }),
    event(10, "sales", "agent_completed"),
    event(11, "root", "agent_started"),
    event(12, "root", "agent_completed"),
  ];
  const rows = autoRunSteps(events, "root", [{ agent_id: "sales-agent", name: "Sales Agent" }] as Agent[]);
  expect(rows.map((row) => row.text)).toContain("Called Sales Agent");
  expect(rows.find((row) => row.text === "Called Sales Agent")?.childRunId).toBe("sales");
  expect(rows.map((row) => row.text)).toContain("Main messaged Sales Agent");
  expect(rows.map((row) => row.text)).toContain("Sales Agent messaged Main");
  expect(rows.find((row) => row.text === "Main waiting for specialists")?.status).toBe("done");
  expect(rows.filter((row) => row.text === "Sales Agent called semantic_query")).toHaveLength(1);
  expect(rows.some((row) => row.status === "running")).toBe(false);
});

it("keeps the delegated name in the chat timeline when the agent catalog is unavailable", () => {
  const rows = autoRunSteps([
    event(1, "root", "agent_started"),
    event(2, "sales", "agent_queued", { agent_id: "sales-agent", agent_name: "Sales Agent", objective: "Compare channels" }),
    event(3, "root", "agent_message", { sender_run_id: "root", recipient_run_id: "sales", content: "Check every channel" }),
    event(4, "sales", "agent_started"),
  ], "root", []);
  expect(rows.map((row) => row.text)).toContain("Called Sales Agent");
  expect(rows.map((row) => row.text)).toContain("Main messaged Sales Agent");
});

it("loads every event page from the last cursor and deduplicates replay", async () => {
  const first = Array.from({ length: 100 }, (_, index) => event(index + 1, "root", "agent_started"));
  const calls = vi.spyOn(agentsApi, "getAutoRunEvents").mockImplementation(async (_root, after) => ({
    events: after === -1 ? first : after === 100 ? [event(101, "root", "agent_completed")] : [],
  }));
  const all = await loadAutoRunEvents("root");
  expect(all).toHaveLength(101);
  expect(calls.mock.calls.map((call) => call[1])).toEqual([-1, 100]);
  const replay = await loadAutoRunEvents("root", all);
  expect(replay).toHaveLength(101);
  expect(calls.mock.calls[2][1]).toBe(101);
});

it("binds a stored Auto turn to its own root instead of the latest root", () => {
  const runs: AutoThreadRun[] = [
    { run_id: "old", objective: "Compare 2024", status: "completed", started_at: "2026-09-24", user_message_id: "q-old", final_message_id: "a-old" },
    { run_id: "new", objective: "Compare 2025", status: "completed", started_at: "2026-09-25", user_message_id: "q-new", final_message_id: "a-new" },
  ];
  const turns = [{ id: "q-old", question: "Compare 2024" }, { id: "q-new", question: "Compare 2025" }];
  expect(rootRunForTurn({ ...turns[0], messageId: "a-old", state: "done" }, runs, turns, null)).toBe("old");
  expect(rootRunForTurn({ ...turns[1], messageId: "a-new", state: "done" }, runs, turns, null)).toBe("new");
  expect(rootRunForTurn({ id: "local", question: "Compare 2026", state: "streaming" }, runs, [...turns, { id: "local", question: "Compare 2026" }], "active")).toBe("active");
  expect(rootRunForTurn({ id: "turn-3", question: "Compare 2024", state: "done" }, runs, [...turns, { id: "turn-3", question: "Compare 2024" }], "active")).toBe("active");
  expect(rootRunForTurn({ id: "other", question: "Different question", state: "done" }, runs, [{ id: "other", question: "Different question" }], null)).toBeNull();
});
