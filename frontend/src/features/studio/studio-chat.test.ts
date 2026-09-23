import { describe, expect, it } from "vitest";
import { applyEvent, replayThread, type TranscriptTurn } from "./studio-chat";
import { visibleContent } from "./turn-content-order";
import type { AssistantEvent } from "@/features/assistant/types";

/**
 * The Studio transcript reducer.
 *
 * The behaviours worth protecting are the ones a reader depends on: an answer
 * that streams in place, a rail that keeps one row per real step instead of one
 * per token, a consent card that clears when the call resolves, and an error
 * that does not wipe an answer already on screen.
 */

const TURN = "t1";

function start(): TranscriptTurn[] {
  return [
    {
      id: TURN,
      question: "How many orders?",
      steps: [],
      answer: "",
      content: [],
      pendingConsent: null,
      blocks: { tables: [], charts: [], citations: [] },
      state: "streaming",
    },
  ];
}

function run(events: AssistantEvent[]): TranscriptTurn {
  const end = events.reduce(
    (turns, event) => applyEvent(turns, TURN, event),
    start(),
  );
  return end[0];
}

const delta = (text: string): AssistantEvent => ({ type: "text_delta", text });
const thinking = (
  phase: "plan" | "skill" | "act" | "observe" | "answer",
  text: string,
  status: "running" | "done" = "running",
): AssistantEvent => ({ type: "thinking", phase, text, status });

describe("studio transcript reducer", () => {
  it("restores attached file cards without placing file content in the chat text", () => {
    const turns = replayThread([
      {
        message_id: "u-file",
        role: "user",
        content: "What is in this file?",
        created_at: "2026-09-23T00:00:00Z",
        attachments: [{ name: "facts.txt", size_bytes: 18 }],
      },
      {
        message_id: "a-file",
        role: "assistant",
        content: "It says 42.",
        created_at: "2026-09-23T00:00:01Z",
      },
    ]);
    expect(turns[0].question).toBe("What is in this file?");
    expect(turns[0].attachments).toEqual([
      { name: "facts.txt", sizeBytes: 18, mediaType: "text/plain" },
    ]);
    expect(turns[0].answer).toBe("It says 42.");
  });

  it("clears evidence from the previous role", () => {
    const turn = run([
      delta("finance-secret"),
      { type: "role_changed", active_role: "marketing", security_context_version: 2 },
      delta("marketing result"),
    ]);
    expect(turn.answer).toBe("marketing result");
    expect(JSON.stringify(turn)).not.toContain("finance-secret");
  });
  it("appends answer text to the turn", () => {
    const turn = run([delta("Total "), delta("revenue")]);
    expect(turn.answer).toBe("Total revenue");
    expect(turn.state).toBe("streaming");
  });

  it("keeps one rail row per thinking phase, not one per token", () => {
    const turn = run([
      thinking("plan", "Understanding the request"),
      thinking("act", "Reasoning about the next step"),
      thinking("act", "Reasoning about the next step, again"),
    ]);
    expect(turn.steps.filter((s) => s.id === "think-act")).toHaveLength(1);
    expect(turn.steps.find((s) => s.id === "think-act")?.text).toBe(
      "Reasoning about the next step, again",
    );
  });

  it("renders the adopted plan as one factual expandable row", () => {
    const turn = run([
      {
        type: "plan",
        steps: [
          {
            id: "understand",
            text: "Understand the request",
            status: "running",
          },
          { id: "answer", text: "Write the answer", status: "pending" },
        ],
      },
    ]);
    expect(turn.steps).toHaveLength(1);
    expect(turn.steps[0]).toMatchObject({
      id: "execution-plan",
      kind: "note",
      text: "Planned 2 steps",
      status: "done",
    });
    expect(turn.steps[0].body).toContain("1. Understand the request");
  });

  it("closes an earlier phase when a later one arrives", () => {
    // The loop announces `act` on every iteration without closing it. The rail
    // must not keep that spinner running once `observe` proves it finished.
    const turn = run([
      thinking("act", "Reasoning about the next step"),
      thinking("observe", "Reading the result", "done"),
    ]);
    expect(turn.steps.find((s) => s.id === "think-act")?.status).toBe("done");
  });

  it("creates a tool row from a status frame alone", () => {
    // A read-only call covered by a grant may arrive as only a status frame in
    // a stream that skipped its tool_call. The row must still exist.
    const turn = run([
      { type: "tool_status", tool_call_id: "c1", status: "running" },
      { type: "tool_status", tool_call_id: "c1", status: "done" },
    ]);
    expect(turn.steps).toHaveLength(1);
    expect(turn.steps[0]).toMatchObject({
      id: "c1",
      kind: "tool",
      status: "done",
    });
    // The provider's call id is an artifact, never a label the reader sees.
    expect(turn.steps[0].label).not.toBe("c1");
    expect(turn.steps[0].text).not.toContain("c1");
  });

  it("stops every spinner when the turn ends", () => {
    // The backstop: a missed close frame must not leave a spinner after the
    // answer. `done` settles anything still running.
    const turn = run([
      thinking("act", "Reasoning about the next step"),
      { type: "done", message_id: "m", finish_reason: "stop" },
    ]);
    expect(turn.steps.every((s) => s.status === "done")).toBe(true);
  });

  it("shows a result summary for other tools", () => {
    // Opening a step answers "what did this do". The detail is what the panel
    // shows, and it must land on the tool row it belongs to.
    const turn = run([
      {
        type: "tool_call",
        payload: {
          tool_call_id: "c1",
          tool_name: "query_execute",
          sql_preview: "",
          classification: "read_only",
          status: "running",
        },
      },
      { type: "tool_status", tool_call_id: "c1", status: "done" },
      {
        type: "tool_detail",
        tool_call_id: "c1",
        text: "1 row returned",
      },
    ]);
    expect(turn.steps[0].detail).toBe("1 row returned");
  });

  it("shows only the skill load in live and saved transcripts", () => {
    const skillBody = "Playbook: classify it.";
    const turn = run([
      {
        type: "tool_call",
        payload: {
          tool_call_id: "c1",
          tool_name: "load_skill",
          skill_name: "native-ml",
          sql_preview: "load skill `native-ml`",
          classification: "read_only",
          status: "running",
        },
      },
      { type: "tool_status", tool_call_id: "c1", status: "done" },
      { type: "tool_detail", tool_call_id: "c1", text: skillBody },
    ]);
    expect(turn.steps[0]).toMatchObject({
      label: "load_skill",
      text: "Loaded a skill: native-ml",
      status: "done",
    });
    expect(turn.steps[0].preview).toBeUndefined();
    expect(turn.steps[0].detail).toBeUndefined();

    const saved = replayThread([
      {
        message_id: "u1",
        role: "user",
        content: "Use native ML",
        created_at: "2026-09-23T00:00:00Z",
      },
      {
        message_id: "a1",
        role: "assistant",
        content: "Done.",
        created_at: "2026-09-23T00:00:01Z",
        steps: [
          {
            kind: "tool",
            tool_call_id: "c1",
            name: "load_skill",
            preview: "load skill `native-ml`",
            arguments: { name: "native-ml" },
            status: "done",
            detail: skillBody,
          },
        ],
      },
    ]);
    expect(saved[0].steps[0].preview).toBeUndefined();
    expect(saved[0].steps[0].detail).toBeUndefined();
    expect(saved[0].steps[0].text).toBe("Loaded a skill: native-ml");
  });

  it("keeps a body on every reasoning phase so any step can open", () => {
    const turn = run([thinking("plan", "Understanding the request")]);
    expect(turn.steps[0].body).toBe("Understanding the request");
  });

  it("raises a consent card for a pending tool call and clears it on status", () => {
    const pending = run([
      {
        type: "tool_call",
        payload: {
          tool_call_id: "c1",
          tool_name: "query_execute",
          sql_preview: "SELECT count(*) FROM orders",
          classification: "read_only",
          status: "pending",
        },
      },
    ]);
    expect(pending.pendingConsent?.tool_call_id).toBe("c1");

    const resolved = applyEvent(
      [
        {
          ...pending,
        },
      ],
      TURN,
      { type: "tool_status", tool_call_id: "c1", status: "running" },
    )[0];
    expect(resolved.pendingConsent).toBeNull();
    expect(resolved.steps[0].status).toBe("running");
    expect(resolved.steps[0].preview).toBe("SELECT count(*) FROM orders");
  });

  it("does not raise a card when a read-only call is auto-approved", () => {
    const turn = run([
      {
        type: "tool_call",
        payload: {
          tool_call_id: "c1",
          tool_name: "query_execute",
          sql_preview: "SELECT 1",
          classification: "read_only",
          status: "running",
        },
      },
    ]);
    expect(turn.pendingConsent).toBeNull();
    expect(turn.steps).toHaveLength(1);
  });

  it("collects result blocks with stable ids", () => {
    const turn = run([
      { type: "table", payload: { columns: ["a"], rows: [["1"]] } },
      { type: "table", payload: { columns: ["b"], rows: [["2"]] } },
      {
        type: "chart",
        payload: { tool_call_id: "c1", chart_spec: '{"mark":"bar"}' },
      },
      {
        type: "citation",
        payload: { title: "Semantic model", source: "orders" },
      },
    ]);
    expect(turn.blocks.tables).toHaveLength(2);
    expect(turn.blocks.tables[0].id).not.toBe(turn.blocks.tables[1].id);
    expect(turn.blocks.charts).toHaveLength(1);
    expect(turn.blocks.citations).toHaveLength(1);
  });

  it("holds a table until earlier text is sealed", () => {
    let turns = start();
    turns = applyEvent(turns, TURN, {
      type: "table",
      content_index: 1,
      content_id: "table-1",
      payload: { columns: ["sku"], rows: [["A"]] },
    });
    expect(visibleContent(turns[0].content)).toEqual([]);

    turns = applyEvent(turns, TURN, {
      type: "text_delta",
      content_index: 0,
      content_id: "text-0",
      text: "Here is the result.",
    });
    expect(visibleContent(turns[0].content).map((item) => item.type)).toEqual([
      "text",
    ]);

    turns = applyEvent(turns, TURN, {
      type: "content_block_done",
      content_index: 0,
      content_id: "text-0",
    });
    expect(visibleContent(turns[0].content).map((item) => item.type)).toEqual([
      "text",
      "table",
    ]);
  });

  it("shows an artifact immediately when it is authored before text", () => {
    const turn = run([
      {
        type: "table",
        content_index: 0,
        content_id: "table-0",
        payload: { columns: ["sku"], rows: [["A"]] },
      },
      {
        type: "text_delta",
        content_index: 1,
        content_id: "text-1",
        text: "This note intentionally follows the table.",
      },
    ]);
    expect(visibleContent(turn.content).map((item) => item.type)).toEqual([
      "table",
      "text",
    ]);
  });

  it("keeps an answer that already streamed when an error follows", () => {
    const turn = run([
      delta("Partial"),
      { type: "error", code: "x", message: "boom" },
    ]);
    expect(turn.answer).toBe("Partial");
    expect(turn.error).toBeUndefined();
    expect(turn.state).toBe("streaming");
  });

  it("records the error when there is no answer to show", () => {
    const turn = run([{ type: "error", code: "x", message: "boom" }]);
    expect(turn.error).toBe("boom");
    expect(turn.state).toBe("error");
  });

  it("marks a cancelled turn from the done frame", () => {
    const turn = run([
      { type: "done", message_id: "m", finish_reason: "cancelled" },
    ]);
    expect(turn.state).toBe("cancelled");
  });

  it("gives every rail row a unique id across a multi-iteration run", () => {
    // The loop re-announces `act` after each observation. The first act settles
    // when `observe` arrives, so the second act is a new row and must not reuse
    // the settled row's id — duplicate keys make React drop or duplicate rows.
    const turn = run([
      thinking("plan", "Understanding the request", "done"),
      thinking("act", "Reasoning about the next step"),
      thinking("observe", "Reading the result", "done"),
      thinking("act", "Reasoning about the next step"),
      thinking("observe", "Reading the result", "done"),
    ]);
    const ids = turn.steps.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.filter((id) => id.startsWith("think-act"))).toHaveLength(2);
  });

  it("ignores lifecycle events it does not render", () => {
    const turn = run([
      { type: "ping" },
      { type: "done", message_id: "m", finish_reason: "stop" },
    ]);
    expect(turn.steps).toEqual([]);
    expect(turn.answer).toBe("");
    expect(turn.stopReason).toBeUndefined();
  });
});
