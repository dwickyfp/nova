import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AssistantProvider, useAssistant } from "./assistant-provider";
import { ASSISTANT_MAX_WIDTH } from "./assistant-panel-state";
import type { WorkspaceTreeResponse } from "@/features/workspaces/types";

function makeTree(
  overrides: Partial<WorkspaceTreeResponse> = {},
): WorkspaceTreeResponse {
  return {
    root_name: "workspace",
    entries: [],
    open_tabs: [],
    active_tab: null,
    sidebar_collapsed: false,
    assistant_collapsed: false,
    defaults: { database: "analytics", schema: "public", role: "ACCOUNTADMIN" },
    ...overrides,
  };
}

/** Routes /workspaces/tree to the supplied tree and records every request. */
function mockTree(tree: WorkspaceTreeResponse) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/workspaces/tree")) {
      return new Response(JSON.stringify(tree), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(null, { status: 204 });
  });
}

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Probe() {
  const {
    open,
    toggle,
    collapsedToPersist,
    setBinding,
    conversation,
    width,
    setWidth,
  } = useAssistant();
  return (
    <div>
      <span data-testid="open">{String(open)}</span>
      <span data-testid="collapsed">{String(collapsedToPersist())}</span>
      <span data-testid="width">{width}</span>
      <button type="button" onClick={() => setWidth(600)}>
        widen
      </button>
      <button type="button" onClick={() => setWidth(9999)}>
        widen-beyond-max
      </button>
      <span data-testid="thread">{conversation.threadId ?? "none"}</span>
      <span data-testid="messages">{conversation.messages.length}</span>
      <button type="button" onClick={toggle}>
        toggle
      </button>
      <button
        type="button"
        onClick={() =>
          setBinding({
            key: "file-1",
            context: { database: "tab_db", schema: null, role: null },
            ensureThread: async () => "thread-a",
          })
        }
      >
        bind
      </button>
      <button type="button" onClick={() => setBinding(null)}>
        unbind
      </button>
      <button type="button" onClick={() => void conversation.sendMessage("hi")}>
        send
      </button>
    </div>
  );
}

function renderProbe(tree: WorkspaceTreeResponse) {
  mockTree(tree);
  return render(
    <QueryClientProvider client={makeClient()}>
      <AssistantProvider>
        <Probe />
      </AssistantProvider>
    </QueryClientProvider>,
  );
}

describe("AssistantProvider", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("restores the open panel from the fetched tree without WorkspacesPage", async () => {
    const { getByTestId } = await renderProbe(
      makeTree({ assistant_collapsed: false }),
    );

    await expect.element(getByTestId("open")).toHaveTextContent("true");
    await expect.element(getByTestId("collapsed")).toHaveTextContent("false");
  });

  it("keeps a collapsed panel closed when the tree says so", async () => {
    const { getByTestId } = await renderProbe(
      makeTree({ assistant_collapsed: true }),
    );

    await expect.element(getByTestId("open")).toHaveTextContent("false");
  });

  it("clamps a resize to the allowed range before persisting it", async () => {
    const { getByTestId, getByRole } = await renderProbe(makeTree());

    await getByRole("button", { name: "widen", exact: true }).click();
    await expect.element(getByTestId("width")).toHaveTextContent("600");

    await getByRole("button", {
      name: "widen-beyond-max",
      exact: true,
    }).click();
    await expect
      .element(getByTestId("width"))
      .toHaveTextContent(String(ASSISTANT_MAX_WIDTH));
  });

  it("fetches the tree with the shared workspace-tree key exactly once", async () => {
    const fetchSpy = mockTree(makeTree());
    await render(
      <QueryClientProvider client={makeClient()}>
        <AssistantProvider>
          <Probe />
        </AssistantProvider>
        <AssistantProvider>
          <Probe />
        </AssistantProvider>
      </QueryClientProvider>,
    );

    await vi.waitFor(() => {
      const treeCalls = fetchSpy.mock.calls.filter(([input]) =>
        String(input).includes("/workspaces/tree"),
      );
      expect(treeCalls).toHaveLength(1);
    });
  });

  it("applies the persisted value only once, so a later refetch cannot clobber a toggle", async () => {
    const client = makeClient();
    const fetchSpy = mockTree(makeTree({ assistant_collapsed: false }));
    const { getByTestId, getByRole } = await render(
      <QueryClientProvider client={client}>
        <AssistantProvider>
          <Probe />
        </AssistantProvider>
      </QueryClientProvider>,
    );
    await expect.element(getByTestId("open")).toHaveTextContent("true");

    await getByRole("button", { name: "toggle" }).click();
    await expect.element(getByTestId("open")).toHaveTextContent("false");

    const before = fetchSpy.mock.calls.filter(([input]) =>
      String(input).includes("/workspaces/tree"),
    ).length;
    await client.refetchQueries({ queryKey: ["workspace-tree"] });
    await vi.waitFor(() => {
      const after = fetchSpy.mock.calls.filter(([input]) =>
        String(input).includes("/workspaces/tree"),
      ).length;
      expect(after).toBeGreaterThan(before);
    });
    await expect.element(getByTestId("open")).toHaveTextContent("false");
  });

  it("keeps the composer live off the workspace by creating a file-less thread", async () => {
    const fetchSpy = mockTree(makeTree());
    fetchSpy.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/workspaces/tree")) {
        return new Response(JSON.stringify(makeTree()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/assistant/threads") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            thread_id: "global-thread",
            title: "Global",
            workspace_file_id: null,
            created_at: "2026-09-19T00:00:00Z",
            updated_at: "2026-09-19T00:00:00Z",
            message_count: 0,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith("/messages")) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(
                'event: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n',
              ),
            );
            controller.close();
          },
        });
        return new Response(body, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      return new Response(null, { status: 204 });
    });

    const { getByTestId, getByRole } = await render(
      <QueryClientProvider client={makeClient()}>
        <AssistantProvider>
          <Probe />
        </AssistantProvider>
      </QueryClientProvider>,
    );

    await getByRole("button", { name: "send" }).click();
    await expect
      .element(getByTestId("thread"))
      .toHaveTextContent("global-thread");

    const created = fetchSpy.mock.calls.find(([input]) =>
      String(input).endsWith("/assistant/threads"),
    );
    expect(created).toBeDefined();
    expect(JSON.parse(String((created?.[1] as RequestInit).body))).toEqual({
      workspace_file_id: null,
    });
  });

  it("prefers a registered workspace binding over the default", async () => {
    const fetchSpy = mockTree(makeTree());
    fetchSpy.mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/workspaces/tree")) {
        return new Response(JSON.stringify(makeTree()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/messages")) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(
                'event: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n',
              ),
            );
            controller.close();
          },
        });
        return new Response(body, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      return new Response(null, { status: 204 });
    });

    const { getByTestId, getByRole } = await render(
      <QueryClientProvider client={makeClient()}>
        <AssistantProvider>
          <Probe />
        </AssistantProvider>
      </QueryClientProvider>,
    );

    await getByRole("button", { name: "bind", exact: true }).click();
    await getByRole("button", { name: "send" }).click();
    await expect.element(getByTestId("thread")).toHaveTextContent("thread-a");

    const messages = fetchSpy.mock.calls.find(([input]) =>
      String(input).endsWith("/messages"),
    );
    expect(
      JSON.parse(String((messages?.[1] as RequestInit).body)),
    ).toMatchObject({
      database: "tab_db",
    });
  });
});

/**
 * Mocks the tree, file-less thread creation and the SSE turn, so a test can
 * drive a global conversation and switch to a file binding and back.
 */
function mockFullApi() {
  let threadSeq = 0;
  const spy = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/workspaces/tree")) {
        return new Response(JSON.stringify(makeTree()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/assistant/threads")) {
        threadSeq += 1;
        return new Response(
          JSON.stringify({
            thread_id: `thread-${threadSeq}`,
            title: "t",
            workspace_file_id: null,
            created_at: "2026-09-19T00:00:00Z",
            updated_at: "2026-09-19T00:00:00Z",
            message_count: 0,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith("/messages")) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(
                'event: text_delta\ndata: {"text":"ok"}\n\nevent: done\ndata: {"message_id":"m","finish_reason":"stop"}\n\n',
              ),
            );
            controller.close();
          },
        });
        return new Response(body, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      return new Response(null, { status: 204 });
    });
  return { spy, threadCount: () => threadSeq };
}

function NavigationProbe() {
  const { setBinding, conversation } = useAssistant();
  return (
    <div>
      <span data-testid="messages">{conversation.messages.length}</span>
      <span data-testid="thread">{conversation.threadId ?? "none"}</span>
      <button
        type="button"
        onClick={() =>
          setBinding({
            key: "file-1",
            context: { database: "tab_db", schema: null, role: null },
            ensureThread: async () => "file-thread",
          })
        }
      >
        open-file
      </button>
      <button type="button" onClick={() => setBinding(null)}>
        no-file
      </button>
      <button type="button" onClick={() => void conversation.sendMessage("hi")}>
        send
      </button>
    </div>
  );
}

describe("AssistantProvider binding identity", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps one global conversation while moving between non-workspace routes", async () => {
    const api = mockFullApi();
    const { getByTestId, getByRole } = await render(
      <QueryClientProvider client={makeClient()}>
        <AssistantProvider>
          <NavigationProbe />
        </AssistantProvider>
      </QueryClientProvider>,
    );

    await getByRole("button", { name: "send" }).click();
    await vi.waitFor(() =>
      expect(getByTestId("messages").element().textContent).toBe("2"),
    );

    // A route change with no workspace file re-renders the same global binding,
    // so the transcript is not reset and no second thread is created.
    await vi.waitFor(() => expect(api.threadCount()).toBe(1));
    await expect.element(getByTestId("messages")).toHaveTextContent("2");
    expect(api.threadCount()).toBe(1);
  });

  it("keeps the global transcript across a file binding and back", async () => {
    const api = mockFullApi();
    const { getByTestId, getByRole } = await render(
      <QueryClientProvider client={makeClient()}>
        <AssistantProvider>
          <NavigationProbe />
        </AssistantProvider>
      </QueryClientProvider>,
    );

    await getByRole("button", { name: "send" }).click();
    await vi.waitFor(() =>
      expect(getByTestId("messages").element().textContent).toBe("2"),
    );

    // A file binding has its own, empty conversation.
    await getByRole("button", { name: "open-file" }).click();
    await expect.element(getByTestId("messages")).toHaveTextContent("0");

    // Returning to the global binding restores its transcript, not an empty one.
    await getByRole("button", { name: "no-file" }).click();
    await expect.element(getByTestId("messages")).toHaveTextContent("2");
    await expect.element(getByTestId("thread")).toHaveTextContent("thread-1");

    // No extra thread was created by the round trip.
    expect(api.threadCount()).toBe(1);
  });
});
