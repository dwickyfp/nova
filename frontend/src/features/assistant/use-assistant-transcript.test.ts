import { describe, expect, it } from "vitest";
import type { AttachedQuery } from "./query-attach";
import {
  transcriptReducer,
  type TranscriptMessage,
} from "./use-assistant-transcript";
import type { AssistantEvent } from "./types";

function reduce(state: TranscriptMessage[], ...events: AssistantEvent[]) {
  return events.reduce(
    (acc, event) => transcriptReducer(acc, { type: "event", event }),
    state,
  );
}

const attachment: AttachedQuery = {
  id: "att-1",
  sql: "SELECT 1",
  tabId: "tab-1",
  fileName: "query.sql",
  database: "db",
  schema: "default",
  role: "ACCOUNTADMIN",
  startLine: 1,
  endLine: 1,
  createdAt: 0,
};

describe("transcriptReducer", () => {
  it("clears prior-role output before displaying new-role results", () => {
    const before = reduce([], { type: "text_delta", text: "finance-secret" });
    const after = reduce(
      before,
      {
        type: "role_changed",
        active_role: "marketing",
        security_context_version: 2,
      },
      { type: "text_delta", text: "marketing result" },
    );
    expect(JSON.stringify(after)).not.toContain("finance-secret");
    expect(after[0].content).toBe("marketing result");
  });
  it("carries attachments and the typed display text on a user message", () => {
    const state = transcriptReducer([], {
      type: "user_message",
      content:
        "[Attached query (file: query.sql)]\n```sql\nSELECT 1\n```\n\n---\n\ndo it",
      attachments: [attachment],
      displayText: "do it",
    });
    expect(state[0].attachments).toEqual([attachment]);
    expect(state[0].display_text).toBe("do it");
  });

  it("leaves a user message without attachments unchanged", () => {
    const state = transcriptReducer([], {
      type: "user_message",
      content: "plain",
    });
    expect(state[0].attachments).toBeUndefined();
    expect(state[0].display_text).toBeUndefined();
  });
  it("appends text deltas into a single streaming assistant message", () => {
    const state = reduce(
      [],
      { type: "text_delta", text: "Hello " },
      { type: "text_delta", text: "world" },
    );
    expect(state).toHaveLength(1);
    expect(state[0].content).toBe("Hello world");
    expect(state[0].turn_state).toBe("streaming");
  });

  it("adds a tool card and updates it by tool_call_id", () => {
    const state = reduce(
      [],
      {
        type: "tool_call",
        payload: {
          tool_call_id: "tc-1",
          tool_name: "query_execute",
          sql_preview: "SELECT 1",
          classification: "read_only",
          status: "pending",
        },
      },
      { type: "tool_status", tool_call_id: "tc-1", status: "running" },
      { type: "tool_status", tool_call_id: "tc-1", status: "done" },
    );
    expect(state).toHaveLength(1);
    expect(state[0].tool_call?.status).toBe("done");
    expect(state[0].tool_call?.sql_preview).toBe("SELECT 1");
  });

  it("marks a streaming message done on the done event", () => {
    const state = reduce(
      [],
      { type: "text_delta", text: "answer" },
      { type: "done", message_id: "m-9", finish_reason: "stop" },
    );
    expect(state[0].turn_state).toBe("done");
    expect(state[0].message_id).toBe("m-9");
  });

  it("marks a partial answer cancelled without deleting it", () => {
    const streaming = reduce([], { type: "text_delta", text: "partial" });
    const state = transcriptReducer(streaming, { type: "cancelled" });
    expect(state).toHaveLength(1);
    expect(state[0].content).toBe("partial");
    expect(state[0].turn_state).toBe("cancelled");
  });

  it("keeps an error as a distinct message", () => {
    const state = reduce([], {
      type: "error",
      code: "provider",
      message: "upstream failed",
    });
    expect(state[0].turn_state).toBe("error");
    expect(state[0].content).toBe("upstream failed");
  });

  it("ignores ping frames", () => {
    expect(reduce([], { type: "ping" })).toEqual([]);
  });

  it("collects the plan and thinking steps into one activity block", () => {
    const state = reduce(
      [],
      {
        type: "plan",
        steps: [{ id: "understand", text: "Understand", status: "running" }],
      },
      {
        type: "thinking",
        phase: "plan",
        text: "Understanding",
        status: "running",
      },
      {
        type: "thinking",
        phase: "skill",
        text: "Loading skill: create-table",
        status: "done",
      },
    );
    expect(state).toHaveLength(1);
    expect(state[0].role).toBe("activity");
    expect(state[0].activity_plan?.[0].id).toBe("understand");
    expect(state[0].activity_steps?.map((s) => s.phase)).toEqual([
      "plan",
      "skill",
    ]);
  });

  it("keeps the activity block above the answer and updates it in place", () => {
    const state = reduce(
      [],
      { type: "thinking", phase: "act", text: "Working", status: "running" },
      { type: "text_delta", text: "Answer" },
      { type: "thinking", phase: "answer", text: "Writing", status: "done" },
    );
    expect(state.map((m) => m.role)).toEqual(["activity", "assistant"]);
    expect(state[0].activity_steps).toHaveLength(2);
  });

  it("settles any running activity step when the turn ends", () => {
    const state = reduce(
      [],
      { type: "thinking", phase: "act", text: "Working", status: "running" },
      { type: "text_delta", text: "done" },
      { type: "done", message_id: "m1", finish_reason: "stop" },
    );
    expect(state[0].activity_steps?.every((s) => s.status === "done")).toBe(
      true,
    );
  });
});
