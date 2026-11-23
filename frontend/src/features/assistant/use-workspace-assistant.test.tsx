import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { AssistantPanel } from "./assistant-panel";
import { MessageList } from "./message-list";
import type { AssistantEvent } from "./types";
import { useWorkspaceAssistant } from "./use-workspace-assistant";

const createThread = vi.fn();
const streamAssistantTurn = vi.fn();

vi.mock("./thread-client", () => ({
  createThread: (...args: unknown[]) => createThread(...args),
  getThread: vi.fn(),
  listThreads: vi.fn(async () => ({ threads: [], count: 0 })),
  searchThreads: vi.fn(async () => ({ threads: [], count: 0 })),
  renameThread: vi.fn(),
  deleteThread: vi.fn(),
}));

vi.mock("./stream-client", async () => {
  const actual =
    await vi.importActual<typeof import("./stream-client")>("./stream-client");
  return {
    ...actual,
    streamAssistantTurn: (...args: unknown[]) => streamAssistantTurn(...args),
  };
});

type Assistant = ReturnType<typeof useWorkspaceAssistant>;

function Harness({
  holder,
  fileId,
  database,
}: {
  holder: { current: Assistant | null };
  fileId: string | null;
  database?: string;
}) {
  const assistant = useWorkspaceAssistant({
    fileId,
    context: { database: database ?? null, schema: null, role: null },
  });
  holder.current = assistant;
  return (
    <MessageList
      messages={assistant.messages}
      statusMessage={assistant.statusMessage}
    />
  );
}

function PanelHarness({
  holder,
  fileId,
  onError,
}: {
  holder: { current: Assistant | null };
  fileId: string | null;
  onError?: (message: string) => void;
}) {
  const assistant = useWorkspaceAssistant({
    fileId,
    context: { database: null, schema: null, role: null },
    onError,
  });
  holder.current = assistant;
  return (
    <AssistantPanel
      open
      onOpenChange={() => {}}
      messages={assistant.messages}
      statusMessage={assistant.statusMessage}
    />
  );
}

afterEach(() => {
  createThread.mockReset();
  streamAssistantTurn.mockReset();
});

function echoStream() {
  streamAssistantTurn.mockImplementation(
    async (
      _thread: string,
      _content: string,
      opts: { onEvent: (e: AssistantEvent) => void },
    ) => {
      opts.onEvent({ type: "text_delta", text: "ok" });
      opts.onEvent({ type: "done", message_id: "m", finish_reason: "stop" });
    },
  );
}

describe("useWorkspaceAssistant", () => {
  it("creates one thread per file and reuses it for the next send", async () => {
    createThread.mockResolvedValue({ thread_id: "thread-a" });
    echoStream();
    const holder: { current: Assistant | null } = { current: null };
    await render(<Harness holder={holder} fileId="file-1" />);

    await holder.current!.sendMessage("first");
    await holder.current!.sendMessage("second");

    expect(createThread).toHaveBeenCalledTimes(1);
    expect(createThread).toHaveBeenCalledWith("file-1");
    expect(streamAssistantTurn).toHaveBeenNthCalledWith(
      2,
      "thread-a",
      "second",
      expect.objectContaining({ database: null }),
    );
  });

  it("forwards the active file context on the turn", async () => {
    createThread.mockResolvedValue({ thread_id: "thread-a" });
    echoStream();
    const holder: { current: Assistant | null } = { current: null };
    await render(
      <Harness holder={holder} fileId="file-1" database="analytics" />,
    );

    await holder.current!.sendMessage("hi");
    expect(streamAssistantTurn).toHaveBeenCalledWith(
      "thread-a",
      "hi",
      expect.objectContaining({ database: "analytics" }),
    );
  });

  it("refuses to send when no file is open", async () => {
    const holder: { current: Assistant | null } = { current: null };
    await render(<Harness holder={holder} fileId={null} />);

    await holder.current!.sendMessage("hi");
    expect(createThread).not.toHaveBeenCalled();
    expect(streamAssistantTurn).not.toHaveBeenCalled();
  });

  it("starts a fresh transcript when the active file changes", async () => {
    createThread.mockResolvedValue({ thread_id: "thread-a" });
    echoStream();
    const holder: { current: Assistant | null } = { current: null };
    const view = await render(<Harness holder={holder} fileId="file-1" />);

    await holder.current!.sendMessage("hi");
    await expect.element(view.getByText("ok")).toBeInTheDocument();

    await view.rerender(<Harness holder={holder} fileId="file-2" />);
    await vi.waitFor(() => {
      expect(view.container.textContent).not.toContain("ok");
    });
  });
});

describe("useWorkspaceAssistant thread lifecycle across files", () => {
  it("returns to the file's own thread after a switch", async () => {
    createThread.mockResolvedValueOnce({ thread_id: "thread-a" });
    createThread.mockResolvedValueOnce({ thread_id: "thread-b" });
    echoStream();
    const holder: { current: Assistant | null } = { current: null };
    const view = await render(<PanelHarness holder={holder} fileId="file-1" />);

    await holder.current!.sendMessage("hi");
    await view.rerender(<PanelHarness holder={holder} fileId="file-2" />);
    await view.rerender(<PanelHarness holder={holder} fileId="file-1" />);

    await holder.current!.sendMessage("again");
    expect(streamAssistantTurn).toHaveBeenLastCalledWith("thread-a", "again", expect.anything());
  });

  it("sends from a newly opened file into its own new thread", async () => {
    createThread.mockResolvedValueOnce({ thread_id: "thread-a" });
    createThread.mockResolvedValueOnce({ thread_id: "thread-b" });
    echoStream();
    const holder: { current: Assistant | null } = { current: null };
    const view = await render(<PanelHarness holder={holder} fileId="file-1" />);

    await holder.current!.sendMessage("hi");
    await view.rerender(<PanelHarness holder={holder} fileId="file-2" />);
    await holder.current!.sendMessage("sent-on-file-2");

    expect(streamAssistantTurn).toHaveBeenLastCalledWith("thread-b", "sent-on-file-2", expect.anything());
  });
});
