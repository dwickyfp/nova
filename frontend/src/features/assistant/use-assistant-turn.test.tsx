import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { AssistantPanel } from "./assistant-panel";
import { MessageList } from "./message-list";
import {
  useAssistantTurn,
  uiActionFromPreview,
  type UiAction,
} from "./use-assistant-turn";

const resetGrant = vi.fn();
const getThread = vi.fn();
const setGrant = vi.fn();

vi.mock("./thread-client", () => ({
  resetGrant: (...args: unknown[]) => resetGrant(...args),
  setGrant: (...args: unknown[]) => setGrant(...args),
  getThread: (...args: unknown[]) => getThread(...args),
  listThreads: vi.fn(async () => ({ threads: [], count: 0 })),
}));

type Turn = ReturnType<typeof useAssistantTurn>;

function Harness({
  holder,
  ensureThread = async () => "t-1",
  context,
  onError,
  canApproveUiAction,
  onUiActionCompleted,
}: {
  holder: { current: Turn | null };
  ensureThread?: () => Promise<string | null>;
  context?: {
    database?: string | null;
    schema?: string | null;
    role?: string | null;
    model?: string | null;
    providerId?: string | null;
  };
  onError?: (message: string) => void;
  canApproveUiAction?: (action: UiAction) => boolean;
  onUiActionCompleted?: (action: UiAction) => void;
}) {
  const turn = useAssistantTurn({
    ensureThread,
    context,
    onError,
    canApproveUiAction,
    onUiActionCompleted,
  });
  holder.current = turn;
  return (
    <MessageList messages={turn.messages} statusMessage={turn.statusMessage} />
  );
}

function PanelHarness({
  holder,
  onError,
}: {
  holder: { current: Turn | null };
  onError?: (message: string) => void;
}) {
  const turn = useAssistantTurn({ ensureThread: async () => "t-1", onError });
  holder.current = turn;
  return (
    <AssistantPanel
      open
      onOpenChange={() => {}}
      messages={turn.messages}
      statusMessage={turn.statusMessage}
    />
  );
}

function sseResponse(frames: string[]) {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

afterEach(() => {
  resetGrant.mockReset();
  getThread.mockReset();
  setGrant.mockReset();
  vi.restoreAllMocks();
});

describe("useAssistantTurn", () => {
  it("parses only Nove UI action previews", () => {
    expect(
      uiActionFromPreview(
        "call_ui_operation",
        'PUT /api/v1/workspaces/files/file-1\n{"body":{}}',
      ),
    ).toEqual({ method: "PUT", path: "/api/v1/workspaces/files/file-1" });
    expect(
      uiActionFromPreview(
        "query_execute",
        "PUT /api/v1/workspaces/files/file-1",
      ),
    ).toBeNull();
  });

  it("reports a completed UI action", async () => {
    const action = { method: "PUT", path: "/api/v1/workspaces/files/file-1" };
    const toolCall = `event: tool_call\ndata: ${JSON.stringify({
      tool_call_id: "call-1",
      tool_name: "call_ui_operation",
      sql_preview: `${action.method} ${action.path}\n{}`,
      classification: "destructive",
      status: "pending",
    })}\n\n`;
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        toolCall,
        'event: tool_status\ndata: {"tool_call_id":"call-1","status":"done"}\n\n',
        'event: done\ndata: {"message_id":"m1","finish_reason":"stop"}\n\n',
      ]),
    );
    const onUiActionCompleted = vi.fn();
    const holder: { current: Turn | null } = { current: null };
    await render(
      <Harness holder={holder} onUiActionCompleted={onUiActionCompleted} />,
    );
    await holder.current!.sendMessage("Edit the SQL file");
    expect(onUiActionCompleted).toHaveBeenCalledWith(action);
  });

  it("blocks approval of a UI action while the workspace has local edits", async () => {
    const action = { method: "PUT", path: "/api/v1/workspaces/files/file-1" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        `event: tool_call\ndata: ${JSON.stringify({
          tool_call_id: "call-1",
          tool_name: "call_ui_operation",
          sql_preview: `${action.method} ${action.path}\n{}`,
          classification: "destructive",
          status: "pending",
        })}\n\n`,
        'event: done\ndata: {"message_id":"m1","finish_reason":"stop"}\n\n',
      ]),
    );
    const canApproveUiAction = vi.fn(() => false);
    const holder: { current: Turn | null } = { current: null };
    await render(
      <Harness holder={holder} canApproveUiAction={canApproveUiAction} />,
    );
    await holder.current!.sendMessage("Edit the SQL file");
    await holder.current!.decide({
      toolCallId: "call-1",
      decision: "approve",
      alwaysAllow: false,
    });
    expect(canApproveUiAction).toHaveBeenCalledWith(action);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("creates a thread lazily and streams deltas into the transcript", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        sseResponse([
          'event: text_delta\ndata: {"text":"Hello "}\n\n',
          'event: text_delta\ndata: {"text":"world"}\n\n',
          'event: done\ndata: {"message_id":"m1","finish_reason":"stop"}\n\n',
        ]),
      );
    const ensureThread = vi.fn(async () => "t-created");
    const holder: { current: Turn | null } = { current: null };
    const { getByText } = await render(
      <Harness holder={holder} ensureThread={ensureThread} />,
    );

    await holder.current!.sendMessage("hi");

    expect(ensureThread).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/assistant/threads/t-created/messages",
      expect.anything(),
    );
    await expect.element(getByText("Hello world")).toBeInTheDocument();
  });

  it("sends the active worksheet context with the turn", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        sseResponse([
          'event: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n',
        ]),
      );
    const holder: { current: Turn | null } = { current: null };
    await render(
      <Harness
        holder={holder}
        context={{ database: "analytics", schema: "public", role: "analyst" }}
      />,
    );

    await holder.current!.sendMessage("hi");
    const body = JSON.parse(
      (fetchMock.mock.calls[0][1] as RequestInit).body as string,
    );
    // Absent model/provider are omitted from the body (undefined keys drop in
    // JSON.stringify), so the backend falls back to its default model.
    expect(body).toEqual({
      content: "hi",
      database: "analytics",
      schema: "public",
      role: "analyst",
    });
  });

  it("pins the selected model and provider on the turn", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        sseResponse([
          'event: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n',
        ]),
      );
    const holder: { current: Turn | null } = { current: null };
    await render(
      <Harness
        holder={holder}
        context={{ database: "analytics", model: "model-y", providerId: "p2" }}
      />,
    );

    await holder.current!.sendMessage("hi");
    const body = JSON.parse(
      (fetchMock.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.model).toBe("model-y");
    expect(body.provider_id).toBe("p2");
  });

  it("does not open a stream when no thread can be resolved", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const onError = vi.fn();
    const holder: { current: Turn | null } = { current: null };
    await render(
      <Harness
        holder={holder}
        ensureThread={async () => null}
        onError={onError}
      />,
    );

    await holder.current!.sendMessage("hi");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalled();
  });

  it("stops a running turn, keeps the partial answer and marks it cancelled", async () => {
    let streamClosed = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              'event: text_delta\ndata: {"text":"partial"}\n\n',
            ),
          );
          init?.signal?.addEventListener("abort", () => {
            streamClosed = true;
            controller.close();
          });
        },
      });
      return new Response(body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    });
    const holder: { current: Turn | null } = { current: null };
    const { getByText } = await render(<Harness holder={holder} />);

    const pending = holder.current!.sendMessage("hi");
    await vi.waitFor(async () => {
      await expect.element(getByText("partial")).toBeInTheDocument();
    });
    holder.current!.stop();
    await pending;

    expect(streamClosed).toBe(true);
    await expect.element(getByText("partial")).toBeInTheDocument();
    await expect
      .element(getByText("Stopped before the answer finished."))
      .toBeInTheDocument();
  });

  it("records a transport error without throwing", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "provider unavailable" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const holder: { current: Turn | null } = { current: null };
    const { getByText } = await render(<Harness holder={holder} />);

    await holder.current!.sendMessage("hi");
    await expect.element(getByText("provider unavailable")).toBeInTheDocument();
  });
});

/** Routes fetch: the SSE turn to a done frame, the decision to a grant response. */
function mockTurnThenDecision(grantActive: boolean) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/messages")) {
        return sseResponse([
          'event: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n',
        ]);
      }
      if (url.endsWith("/decision")) {
        const body = JSON.parse((init?.body as string) ?? "{}");
        return new Response(
          JSON.stringify({
            tool_call_id: "call-1",
            status: "approved",
            grant_active:
              body.decision === "allow_session" ? grantActive : false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(null, { status: 204 });
    });
}

describe("useAssistantTurn grant reset", () => {
  it("tracks the grant from an allow_session decision in the panel", async () => {
    mockTurnThenDecision(true);
    const holder: { current: Turn | null } = { current: null };
    const { getByRole } = await render(<PanelHarness holder={holder} />);
    await holder.current!.sendMessage("hi");

    expect(holder.current!.grantActive).toBe(false);
    await holder.current!.decide({
      toolCallId: "call-1",
      decision: "approve",
      alwaysAllow: true,
    });

    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true));
    await expect
      .element(getByRole("button", { name: "Reset permissions" }))
      .not.toBeInTheDocument();
  });

  it("revokes the grant and clears its active state", async () => {
    mockTurnThenDecision(true);
    resetGrant.mockResolvedValue(undefined);
    const holder: { current: Turn | null } = { current: null };
    const { getByText, container } = await render(
      <PanelHarness holder={holder} />,
    );
    await holder.current!.sendMessage("hi");
    await holder.current!.decide({
      toolCallId: "call-1",
      decision: "approve",
      alwaysAllow: true,
    });
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true));

    await holder.current!.resetPermissions();

    expect(resetGrant).toHaveBeenCalledWith("t-1");
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(false));
    await expect
      .element(getByText("Read-only queries are allowed in this conversation."))
      .not.toBeInTheDocument();
    expect(container.textContent).not.toContain("Reset permissions");
  });

  it("surfaces a failed reset instead of swallowing it", async () => {
    mockTurnThenDecision(true);
    resetGrant.mockRejectedValue(new Error("Thread not found"));
    const onError = vi.fn();
    const holder: { current: Turn | null } = { current: null };
    const { getByText } = await render(
      <PanelHarness holder={holder} onError={onError} />,
    );
    await holder.current!.sendMessage("hi");
    await holder.current!.decide({
      toolCallId: "call-1",
      decision: "approve",
      alwaysAllow: true,
    });
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true));

    await holder.current!.resetPermissions();

    await expect.element(getByText("Thread not found")).toBeInTheDocument();
    expect(onError).toHaveBeenCalledWith("Thread not found");
    expect(holder.current!.grantActive).toBe(true);
  });

  it("switches to an existing thread and replays its stored messages", async () => {
    getThread.mockResolvedValue({
      thread: { thread_id: "t-2", title: "Old chat" },
      messages: [
        {
          message_id: "m1",
          role: "user",
          content: "previous question",
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          message_id: "m2",
          role: "assistant",
          content: "previous answer",
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    });
    const holder: { current: Turn | null } = { current: null };
    const { getByText } = await render(<Harness holder={holder} />);

    await holder.current!.loadThread("t-2");

    expect(getThread).toHaveBeenCalledWith("t-2");
    await expect.element(getByText("previous question")).toBeInTheDocument();
    await expect.element(getByText("previous answer")).toBeInTheDocument();
    expect(holder.current!.threadId).toBe("t-2");
  });

  it("surfaces a failed thread load instead of leaving a blank panel", async () => {
    getThread.mockRejectedValue(new Error("Thread not found"));
    const onError = vi.fn();
    const holder: { current: Turn | null } = { current: null };
    await render(<Harness holder={holder} onError={onError} />);

    await holder.current!.loadThread("gone");

    await vi.waitFor(() =>
      expect(onError).toHaveBeenCalledWith("Thread not found"),
    );
  });

  it("clears the conversation and forgets the thread for a new chat", async () => {
    getThread.mockResolvedValue({
      thread: { thread_id: "t-2", title: "Old chat" },
      messages: [
        {
          message_id: "m1",
          role: "user",
          content: "previous question",
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    const holder: { current: Turn | null } = { current: null };
    const { getByText, container } = await render(<Harness holder={holder} />);
    await holder.current!.loadThread("t-2");
    await expect.element(getByText("previous question")).toBeInTheDocument();

    holder.current!.startNewThread();

    await vi.waitFor(() => expect(holder.current!.threadId).toBeNull());
    expect(container.textContent).not.toContain("previous question");
  });

  it("revokes an active grant when starting a new chat", async () => {
    resetGrant.mockResolvedValue(undefined);
    mockTurnThenDecision(true);
    const holder: { current: Turn | null } = { current: null };
    await render(<Harness holder={holder} />);
    await holder.current!.sendMessage("hi");
    await holder.current!.decide({
      toolCallId: "call-1",
      decision: "approve",
      alwaysAllow: true,
    });
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true));

    holder.current!.startNewThread();

    await vi.waitFor(() => expect(resetGrant).toHaveBeenCalledWith("t-1"));
  });
});

describe("useAssistantTurn approval mode", () => {
  it("presets the conversation grant and reflects it in the mode", async () => {
    setGrant.mockResolvedValue(true);
    const holder: { current: Turn | null } = { current: null };
    await render(<Harness holder={holder} />);
    expect(holder.current!.approvalMode).toBe("ask");

    await holder.current!.setApprovalMode("allow_read_only");

    expect(setGrant).toHaveBeenCalledWith("t-1", true);
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true));
    expect(holder.current!.approvalMode).toBe("allow_read_only");
  });

  it("clears the grant when switching back to ask", async () => {
    setGrant.mockResolvedValueOnce(true);
    const holder: { current: Turn | null } = { current: null };
    await render(<Harness holder={holder} />);
    await holder.current!.setApprovalMode("allow_read_only");
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(true));

    setGrant.mockResolvedValueOnce(false);
    await holder.current!.setApprovalMode("ask");

    expect(setGrant).toHaveBeenLastCalledWith("t-1", false);
    await vi.waitFor(() => expect(holder.current!.grantActive).toBe(false));
    expect(holder.current!.approvalMode).toBe("ask");
  });

  it("leaves the mode unchanged and surfaces the error when persisting fails", async () => {
    setGrant.mockRejectedValue(new Error("Not allowed"));
    const onError = vi.fn();
    const holder: { current: Turn | null } = { current: null };
    await render(<Harness holder={holder} onError={onError} />);

    await holder.current!.setApprovalMode("allow_read_only");

    await vi.waitFor(() => expect(onError).toHaveBeenCalledWith("Not allowed"));
    expect(holder.current!.grantActive).toBe(false);
    expect(holder.current!.approvalMode).toBe("ask");
  });

  it("aborts the mode change when no thread can be resolved", async () => {
    const onError = vi.fn();
    const holder: { current: Turn | null } = { current: null };
    await render(
      <Harness
        holder={holder}
        ensureThread={async () => null}
        onError={onError}
      />,
    );

    await holder.current!.setApprovalMode("allow_read_only");

    expect(setGrant).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalled();
  });
});
