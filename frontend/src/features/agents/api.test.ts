import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { agentsApi, resumeAgentTurn, streamAgentTurn } from "./api";

/**
 * Locks the wire paths of the Agent Studio client (Phase 12). A path change here
 * without a matching backend change is a 404 in production, so the paths are
 * asserted rather than left to integration testing.
 */

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => ({}),
  });
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

function requestedUrl(index = 0) {
  return fetchMock.mock.calls[index][0] as string;
}

describe("agents api paths", () => {
  it("loads unified history without an agent filter", async () => {
    await agentsApi.listThreads();
    expect(requestedUrl()).toBe("/api/v1/agents/threads?limit=50");
  });
  it("uses the Semantic View rule proposal path", async () => {
    await agentsApi.listRuleProposals("view/with space");
    expect(requestedUrl()).toBe("/api/v1/semantic-views/view%2Fwith%20space/rule-proposals");
  });

  it("encodes the agent id in the threads path", async () => {
    await agentsApi.createThread("agent/with space");
    expect(requestedUrl()).toBe("/api/v1/agents/agent%2Fwith%20space/threads");
  });

  it("encodes both ids in the thread detail path", async () => {
    await agentsApi.getThread("a b", "c/d");
    expect(requestedUrl()).toBe("/api/v1/agents/a%20b/threads/c%2Fd?limit=50");
  });

  it("posts a tool-call decision to the agent-scoped path", async () => {
    await agentsApi.decideToolCall("ag1", "tc1", "allow_session");
    expect(requestedUrl()).toBe("/api/v1/agents/ag1/tool-calls/tc1/decision");
  });

});

function sseResponse(frames: string, runId?: string) {
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(frames));
      controller.close();
    },
  }), {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      ...(runId ? { "X-Nova-Run-ID": runId } : {}),
    },
  });
}

describe("agent run reconnect", () => {
  it("restores a run after reload using only GET and preserves large cursors", async () => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(sseResponse(
      'event: text_delta\ndata: {"run_id":"run","sequence":"9999999999999999","text":"Recovered"}\n\n'
      + 'event: done\ndata: {"run_id":"run","sequence":"10000000000000000","message_id":"m","finish_reason":"stop"}\n\n',
    ));
    const seen: string[] = [];
    await resumeAgentTurn("sales", "thread", "run", { onEvent: (event) => seen.push(event.type) });
    expect(seen).toEqual(["text_delta", "done"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(requestedUrl()).toContain("/runs/run/events?after=-1");
    expect(fetchMock.mock.calls[0][1].method).toBeUndefined();
  });

  it("sends cancellation to the existing run without posting another message", async () => {
    await agentsApi.cancelRun("sales", "thread", "run");
    expect(requestedUrl()).toBe("/api/v1/agents/sales/threads/thread/runs/run/cancel");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });

  it("replays missing frames without reposting the user message", async () => {
    const frame = (type: string, sequence: number, payload: Record<string, unknown>) =>
      `event: ${type}\ndata: ${JSON.stringify({ run_id: "run-1", sequence, ...payload })}\n\n`;
    fetchMock.mockReset();
    fetchMock
      .mockResolvedValueOnce(sseResponse(frame("plan", 0, { steps: [] }), "run-1"))
      .mockResolvedValueOnce(sseResponse(
        frame("plan", 0, { steps: [] })
        + frame("text_delta", 1, { text: "Done" })
        + frame("done", 2, { message_id: "m1", finish_reason: "stop" }),
      ));
    const seen: string[] = [];
    await streamAgentTurn("sales", "thread-1", "Question", {
      onEvent: (event) => seen.push(event.type),
    });
    expect(seen).toEqual(["plan", "text_delta", "done"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(requestedUrl(1)).toContain("/runs/run-1/events?after=0");
  });
});
