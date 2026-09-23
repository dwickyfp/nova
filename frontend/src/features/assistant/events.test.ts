import { describe, expect, it } from "vitest";
import { parseAssistantEvent, readSseFrames } from "./events";

describe("parseAssistantEvent", () => {
  it("parses role changes and requires their security version", () => {
    expect(
      parseAssistantEvent(
        "role_changed",
        JSON.stringify({
          active_role: "marketing",
          security_context_version: 2,
        }),
      ),
    ).toEqual({
      type: "role_changed",
      active_role: "marketing",
      security_context_version: 2,
    });
    expect(
      parseAssistantEvent("role_changed", '{"active_role":"marketing"}'),
    ).toBeNull();
    expect(
      parseAssistantEvent(
        "tool_call",
        JSON.stringify({
          tool_call_id: "switch",
          tool_name: "query_execute",
          sql_preview: "USE ROLE marketing",
          classification: "session_change",
          status: "pending",
        }),
      ),
    ).toMatchObject({ payload: { classification: "session_change" } });
  });
  it("parses a text delta", () => {
    expect(
      parseAssistantEvent("text_delta", JSON.stringify({ text: "hello" })),
    ).toEqual({
      type: "text_delta",
      text: "hello",
    });
  });

  it("parses a tool call with classification and status", () => {
    const event = parseAssistantEvent(
      "tool_call",
      JSON.stringify({
        tool_call_id: "tc-1",
        tool_name: "query_execute",
        sql_preview: "SELECT 1",
        classification: "read_only",
        status: "pending",
      }),
    );
    expect(event).toEqual({
      type: "tool_call",
      payload: {
        tool_call_id: "tc-1",
        tool_name: "query_execute",
        sql_preview: "SELECT 1",
        classification: "read_only",
        status: "pending",
        result_summary: null,
        error: null,
      },
    });
  });

  it("parses a thinking frame", () => {
    expect(
      parseAssistantEvent(
        "thinking",
        JSON.stringify({
          phase: "skill",
          text: "Loading skill: create-table",
          status: "done",
        }),
      ),
    ).toEqual({
      type: "thinking",
      phase: "skill",
      text: "Loading skill: create-table",
      status: "done",
    });
  });

  it("rejects an unknown thinking phase or status", () => {
    expect(
      parseAssistantEvent(
        "thinking",
        '{"phase":"x","text":"t","status":"done"}',
      ),
    ).toBeNull();
    expect(
      parseAssistantEvent(
        "thinking",
        '{"phase":"act","text":"t","status":"x"}',
      ),
    ).toBeNull();
  });

  it("parses a plan frame and rejects a malformed step", () => {
    expect(
      parseAssistantEvent(
        "plan",
        JSON.stringify({
          steps: [{ id: "a", text: "Understand", status: "running" }],
        }),
      ),
    ).toEqual({
      type: "plan",
      steps: [{ id: "a", text: "Understand", status: "running" }],
    });
    expect(parseAssistantEvent("plan", '{"steps":[{"id":"a"}]}')).toBeNull();
    expect(parseAssistantEvent("plan", '{"steps":"nope"}')).toBeNull();
  });

  it("parses a tool status transition", () => {
    expect(
      parseAssistantEvent(
        "tool_status",
        JSON.stringify({ tool_call_id: "tc-1", status: "running" }),
      ),
    ).toEqual({ type: "tool_status", tool_call_id: "tc-1", status: "running" });
  });

  it("parses factual tool progress with generated SQL", () => {
    expect(
      parseAssistantEvent(
        "tool_progress",
        JSON.stringify({
          tool_call_id: "tc-1",
          stage: "sql_generated",
          text: "Generated SQL",
          sql_preview: "SELECT sku, SUM(revenue) FROM sales GROUP BY sku",
        }),
      ),
    ).toEqual({
      type: "tool_progress",
      tool_call_id: "tc-1",
      stage: "sql_generated",
      text: "Generated SQL",
      sql_preview: "SELECT sku, SUM(revenue) FROM sales GROUP BY sku",
    });
  });

  it("parses ordered content positions and block completion", () => {
    expect(
      parseAssistantEvent(
        "text_delta",
        '{"text":"Before","content_index":0,"content_id":"text-0"}',
      ),
    ).toEqual({
      type: "text_delta",
      text: "Before",
      content_index: 0,
      content_id: "text-0",
    });
    expect(
      parseAssistantEvent(
        "content_block_done",
        '{"content_index":0,"content_id":"text-0"}',
      ),
    ).toEqual({
      type: "content_block_done",
      content_index: 0,
      content_id: "text-0",
    });
  });

  it("parses done, error and ping", () => {
    expect(
      parseAssistantEvent("done", '{"message_id":"m1","finish_reason":"stop"}'),
    ).toEqual({
      type: "done",
      message_id: "m1",
      finish_reason: "stop",
    });
    expect(
      parseAssistantEvent("error", '{"code":"x","message":"boom"}'),
    ).toEqual({
      type: "error",
      code: "x",
      message: "boom",
    });
    expect(parseAssistantEvent("ping", "")).toEqual({ type: "ping" });
  });

  it("carries token usage on done only when the provider reported it", () => {
    expect(
      parseAssistantEvent(
        "done",
        '{"message_id":"m1","finish_reason":"stop","usage":{"total_tokens":412}}',
      ),
    ).toMatchObject({ type: "done", total_tokens: 412 });

    // No usage field: the panel must show nothing, not a zero.
    const withoutUsage = parseAssistantEvent(
      "done",
      '{"message_id":"m1","finish_reason":"stop"}',
    );
    expect(
      withoutUsage?.type === "done" && withoutUsage.total_tokens,
    ).toBeUndefined();
  });

  it("ignores unknown event names and malformed payloads instead of throwing", () => {
    expect(parseAssistantEvent("future_event", '{"a":1}')).toBeNull();
    expect(parseAssistantEvent("text_delta", "not json")).toBeNull();
    expect(parseAssistantEvent("tool_status", '{"status":"nope"}')).toBeNull();
    expect(
      parseAssistantEvent("tool_call", '{"tool_name":"query_execute"}'),
    ).toBeNull();
  });
});

async function framesFrom(chunks: string[]) {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  const frames = [];
  for await (const frame of readSseFrames(stream)) frames.push(frame);
  return frames;
}

describe("parseAssistantEvent structured blocks (Phase 12)", () => {
  it("parses a table block and coerces columns to strings", () => {
    const event = parseAssistantEvent(
      "table",
      JSON.stringify({
        title: "revenue",
        columns: ["region", "total"],
        rows: [["APAC", "48098.00"]],
      }),
    );
    expect(event).toEqual({
      type: "table",
      payload: {
        title: "revenue",
        columns: ["region", "total"],
        rows: [["APAC", "48098.00"]],
      },
    });
  });

  it("rejects a table block with no rows array", () => {
    expect(
      parseAssistantEvent("table", JSON.stringify({ columns: ["a"] })),
    ).toBeNull();
  });

  it("parses a chart block", () => {
    const event = parseAssistantEvent(
      "chart",
      JSON.stringify({ tool_call_id: "tc-1", chart_spec: '{"mark":"bar"}' }),
    );
    expect(event).toEqual({
      type: "chart",
      payload: { tool_call_id: "tc-1", chart_spec: '{"mark":"bar"}' },
    });
  });

  it("rejects a chart block missing the spec", () => {
    expect(
      parseAssistantEvent("chart", JSON.stringify({ tool_call_id: "x" })),
    ).toBeNull();
  });

  it("parses a citation block", () => {
    const event = parseAssistantEvent(
      "citation",
      JSON.stringify({ title: "Policy", source: "doc-1", snippet: "text" }),
    );
    expect(event).toEqual({
      type: "citation",
      payload: { title: "Policy", source: "doc-1", snippet: "text" },
    });
  });

  it("ignores unknown event names without throwing", () => {
    expect(
      parseAssistantEvent("future_event", JSON.stringify({ x: 1 })),
    ).toBeNull();
  });
});

describe("readSseFrames", () => {
  it("splits frames and preserves the event name", async () => {
    const frames = await framesFrom([
      'event: text_delta\ndata: {"text":"a"}\n\nevent: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n',
    ]);
    expect(frames).toEqual([
      { event: "text_delta", data: '{"text":"a"}' },
      { event: "done", data: '{"message_id":"m","finish_reason":"stop"}' },
    ]);
  });

  it("reassembles a frame split across chunks", async () => {
    const frames = await framesFrom([
      "event: text_delta\nda",
      'ta: {"text":"hi"}\n',
      "\n",
    ]);
    expect(frames).toEqual([{ event: "text_delta", data: '{"text":"hi"}' }]);
  });

  it("flushes a trailing frame with no terminating blank line (cancelled stream)", async () => {
    const frames = await framesFrom(["event: ping\ndata: {}"]);
    expect(frames).toEqual([{ event: "ping", data: "{}" }]);
  });
});
