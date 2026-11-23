import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  decideToolCall,
  streamAssistantTurn,
  toConsentPayload,
} from "./stream-client";

const setUser = vi.hoisted(() => vi.fn());

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: {
    getState: () => ({
      auth: {
        accessToken: "test-token",
        reset: vi.fn(),
        user: {
          username: "alice",
          roles: ["finance", "marketing"],
          activeRole: "finance",
        },
        setUser,
      },
    }),
  },
}));

const fetchMock = vi.fn();

beforeEach(() => {
  setUser.mockClear();
  fetchMock.mockClear();
  fetchMock.mockResolvedValue(
    new Response(
      JSON.stringify({ tool_call_id: "call-1", status: "approved" }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("toConsentPayload", () => {
  it("preserves adjacent run cursors above JavaScript's safe integer limit", async () => {
    const frame = (sequence: string, text: string) =>
      `event: text_delta\ndata: ${JSON.stringify({run_id: "run-1", sequence, text})}\n\n`;
    fetchMock.mockResolvedValueOnce(new Response(
      frame("183551631220940801", "First") + frame("183551631220940802", "Second") +
      frame("183551631220940802", "Duplicate"),
      {status: 200, headers: {"Content-Type": "text/event-stream"}},
    ));
    const onEvent = vi.fn();
    await streamAssistantTurn("thread", "Continue", {onEvent});
    expect(onEvent.mock.calls.map(([event]) => event.text)).toEqual(["First", "Second"]);
  });
  it("sends attached files with the turn in the server's shape", async () => {
    fetchMock.mockResolvedValueOnce(new Response("", {status: 200, headers: {"Content-Type": "text/event-stream"}}));
    await streamAssistantTurn("thread", "How many orders?", {
      onEvent: vi.fn(),
      files: [{name: "orders.csv", content: "city,orders\n", mediaType: "text/plain"}],
    });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.attachments).toEqual([
      {name: "orders.csv", content: "city,orders\n", media_type: "text/plain"},
    ]);
    fetchMock.mockResolvedValueOnce(new Response("", {status: 200, headers: {"Content-Type": "text/event-stream"}}));
    await streamAssistantTurn("thread", "No files", {onEvent: vi.fn()});
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).not.toHaveProperty("attachments");
  });
  it("drops duplicate UI program chunks during journal replay", async () => {
    const chunk =
      'event: ui_delta\ndata: {"run_id":"run-ui","sequence":2,"content_index":1,"content_id":"content-1","text":"root = Col([])"}\n\n';
    fetchMock.mockResolvedValueOnce(
      new Response(chunk + chunk, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );
    const onEvent = vi.fn();
    await streamAssistantTurn("thread", "Build a calculator", { onEvent });
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({ type: "ui_delta", sequence: 2 }),
    );
  });
  it("updates the sidebar role from the server role-change event", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        'event: role_changed\ndata: {"active_role":"marketing","security_context_version":2}\n\n',
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    const onEvent = vi.fn();
    await streamAssistantTurn("thread", "USE ROLE marketing", { onEvent });
    expect(setUser).toHaveBeenCalledWith({
      username: "alice",
      roles: ["finance", "marketing"],
      activeRole: "marketing",
      securityContextVersion: 2,
    });
    expect(onEvent).toHaveBeenCalledWith({
      type: "role_changed",
      active_role: "marketing",
      security_context_version: 2,
    });
  });
  it("maps allow to allow_once and deny to deny; there is no conversation-wide grant", () => {
    expect(toConsentPayload("approve")).toBe("allow_once");
    expect(toConsentPayload("deny")).toBe("deny");
  });
});

describe("decideToolCall", () => {
  it("posts the tool-call-scoped path with the frozen decision enum", async () => {
    await decideToolCall("call-1", "approve");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/assistant/tool-calls/call-1/decision");
    expect(url).not.toContain("/threads/");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ decision: "allow_once" });
  });

  it("sends secure input only in the approval request", async () => {
    await decideToolCall("create-user", "approve", {
      password: "private-marker",
    });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      decision: "allow_once",
      secure_input: { password: "private-marker" },
    });
  });

  it("sends a stage file as multipart approval outside the model request", async () => {
    const file = new File(["private-file-data"], "data.csv", {
      type: "text/csv",
    });
    await decideToolCall("stage-upload", "approve", undefined, file);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "/api/v1/assistant/tool-calls/stage-upload/upload-decision",
    );
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBe(file);
    expect(
      (init.headers as Record<string, string>)["Content-Type"],
    ).toBeUndefined();
  });

  it("sends deny without an always-allow field", async () => {
    await decideToolCall("call-3", "deny");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ decision: "deny" });
    expect(body).not.toHaveProperty("always_allow");
  });

  it("url-encodes the tool call id", async () => {
    await decideToolCall("call/with space", "approve");

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "/api/v1/assistant/tool-calls/call%2Fwith%20space/decision",
    );
  });

});
