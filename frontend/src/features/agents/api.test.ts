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
      'event: part.start\ndata: {"run_id":"run","seq":9007199254740989,"part_id":"run:0","type":"text"}\n\n'
      + 'event: part.delta\ndata: {"run_id":"run","seq":9007199254740990,"part_id":"run:0","text":"Recovered"}\n\n'
      + 'event: run.finished\ndata: {"run_id":"run","seq":9007199254740991,"message_id":"m","finish_reason":"stop"}\n\n',
    ));
    const seen: string[] = [];
    await resumeAgentTurn("sales", "thread", "run", { onEvent: (event) => seen.push(event.type) });
    expect(seen).toEqual(["part.start", "part.delta", "run.finished"]);
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
    const frame = (type: string, seq: number, payload: Record<string, unknown>) =>
      `event: ${type}\ndata: ${JSON.stringify({ run_id: "run-1", seq, ...payload })}\n\n`;
    const started = frame("run.started", 0, { protocol_version: 2, role: "ANALYST" });
    fetchMock.mockReset();
    fetchMock
      .mockResolvedValueOnce(sseResponse(started, "run-1"))
      .mockResolvedValueOnce(sseResponse(
        started
        + frame("part.start", 1, { part_id: "run-1:0", type: "text" })
        + frame("part.delta", 2, { part_id: "run-1:0", text: "Done" })
        + frame("run.finished", 3, { message_id: "m1", finish_reason: "stop" }),
      ));
    const seen: string[] = [];
    await streamAgentTurn("sales", "thread-1", "Question", {
      onEvent: (event) => seen.push(event.type),
    });
    // The replayed run.started is already delivered and is not handed on twice.
    expect(seen).toEqual(["run.started", "part.start", "part.delta", "run.finished"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(requestedUrl(1)).toContain("/runs/run-1/events?after=0");
  });
});
