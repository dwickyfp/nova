import { describe, expect, it } from "vitest";
import { replayThread } from "./studio-chat";
import type { AgentMessage } from "@/features/agents/api";

/**
 * Rebuilding a transcript from a stored thread.
 *
 * The live transcript comes from SSE frames; this path rebuilds the same shape
 * from the trace, which is what makes a reopened conversation show its work.
 * The cases worth pinning: a user message opens a turn, the assistant message
 * fills it, and every recorded step kind lands somewhere visible.
 */

const user = (content: string): AgentMessage => ({
  message_id: "u1",
  role: "user",
  content,
  created_at: "2026-01-01T00:00:00",
});

const assistant = (
  content: string,
  steps: AgentMessage["steps"],
  extra: Partial<AgentMessage> = {},
): AgentMessage => ({
  message_id: "a1",
  role: "assistant",
  content,
  created_at: "2026-01-01T00:00:01",
  steps,
  ...extra,
});

describe("replayThread", () => {
  it("opens a turn from the question and fills it from the answer", () => {
    const turns = replayThread([
      user("How many orders?"),
      assistant("42.", []),
    ]);
    expect(turns).toHaveLength(1);
    expect(turns[0].question).toBe("How many orders?");
    expect(turns[0].answer).toBe("42.");
    expect(turns[0].state).toBe("done");
  });

  it("rebuilds reasoning rows as settled, never spinning", () => {
    const turns = replayThread([
      user("q"),
      assistant("a", [
        { kind: "reasoning", phase: "plan", text: "Understanding the request" },
        {
          kind: "reasoning",
          phase: "act",
          text: "Reasoning about the next step",
        },
      ]),
    ]);
    expect(turns[0].steps).toHaveLength(2);
    expect(turns[0].steps.every((s) => s.status === "done")).toBe(true);
    // The narration is preserved as the row's body, as it is live.
    expect(turns[0].steps[1].body).toBe("Reasoning about the next step");
  });

  it("rebuilds a tool row with its redacted SQL", () => {
    const turns = replayThread([
      user("q"),
      assistant("a", [
        {
          kind: "tool",
          name: "query_execute",
          preview: "SELECT 1",
          arguments: {},
          status: "done",
        },
      ]),
    ]);
    expect(turns[0].steps[0]).toMatchObject({
      kind: "tool",
      label: "query_execute",
      status: "done",
      preview: "SELECT 1",
    });
  });

  it("rebuilds the result blocks, not just the prose", () => {
    const turns = replayThread([
      user("q"),
      assistant("a", [
        {
          kind: "table",
          title: "Omzet per SKU",
          columns: ["sku", "omzet"],
          rows: [["D. COKLAT 12", 3123]],
        },
        { kind: "chart", tool_call_id: "c1", chart_spec: '{"mark":"bar"}' },
        { kind: "citation", citations: [{ title: "Semantic model" }] },
      ]),
    ]);
    expect(turns[0].blocks.tables).toHaveLength(1);
    expect(turns[0].blocks.tables[0].block.title).toBe("Omzet per SKU");
    expect(turns[0].blocks.charts).toHaveLength(1);
    expect(turns[0].blocks.citations).toHaveLength(1);
  });

  it("carries the token count and model onto the turn", () => {
    const turns = replayThread([
      user("q"),
      assistant("a", [], { total_tokens: 412, model_name: "gpt-4o" }),
    ]);
    expect(turns[0].tokens).toBe(412);
    expect(turns[0].model).toBe("gpt-4o");
  });

  it("gives each replayed turn a stable id from its message", () => {
    const turns = replayThread([user("one"), assistant("a", []), user("two")]);
    expect(turns.map((t) => t.id)).toEqual(["u1", "u1"]);
    expect(turns).toHaveLength(2);
  });

  it("opens an orphan assistant message as its own turn", () => {
    // A legacy or truncated thread can start mid-conversation. It must still
    // open, without inventing a question.
    const turns = replayThread([assistant("orphan answer", [])]);
    expect(turns).toHaveLength(1);
    expect(turns[0].question).toBe("");
    expect(turns[0].answer).toBe("orphan answer");
  });

  it("tolerates a thread whose trace is missing", () => {
    const turns = replayThread([
      { message_id: "u", role: "user", content: "q", created_at: "" },
      {
        message_id: "a",
        role: "assistant",
        content: "a",
        created_at: "",
      },
    ]);
    expect(turns[0].steps).toEqual([]);
    expect(turns[0].answer).toBe("a");
  });
});
