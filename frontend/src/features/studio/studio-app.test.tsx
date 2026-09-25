import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { agentsApi } from "@/features/agents/api";
import { StudioApp } from "./index";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  search: { agent: "a1", thread: "thread-1" } as {
    agent?: string;
    thread?: string;
    view?: "chat" | "artifacts" | "capabilities";
  },
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
  useSearch: () => mocks.search,
}));

vi.mock("@/features/agents/api", () => ({
  AUTO_AGENT_ID: "__auto__",
  agentsApi: {
    listStudio: vi.fn(async () => ({
      agents: [
        {
          agent_id: "a1",
          name: "Revenue Analyst",
          database_name: "sales",
        },
      ],
    })),
    listThreads: vi.fn(async () => ({
      threads: [{ thread_id: "thread-1", title: "Revenue" }],
    })),
  },
  studioApi: {
    skillAuthor: vi.fn(async () => ({
      agent_id: "nova-skill-author",
      name: "Nova Studio",
    })),
    settings: vi.fn(async () => ({
      identity: { active_role: null },
      preferences: {},
    })),
  },
}));

vi.mock("./studio-chat", () => ({
  StudioChat: ({
    agent,
    onThreadChange,
    initialPrompt,
  }: {
    agent: { name: string } | null;
    onThreadChange: (id: string | null) => void;
    initialPrompt?: string;
  }) => (
    <div>
      <span>{agent?.name ?? "No agent"}</span>
      <span>{initialPrompt}</span>
      <button type="button" onClick={() => onThreadChange(null)}>
        Header new chat
      </button>
      <button type="button" onClick={() => onThreadChange("thread-2")}>
        Sample creates thread
      </button>
    </div>
  ),
}));
vi.mock("./studio-sidebar", () => ({
  StudioSidebar: ({
    threads,
    onNewChat,
    onView,
  }: {
    threads: unknown[];
    onNewChat: () => void;
    onView: (view: "artifacts" | "capabilities") => void;
  }) => (
    <div>
      <span>Threads: {threads.length}</span>
      <button type="button" onClick={onNewChat}>
        Sidebar new chat
      </button>
      <button type="button" onClick={() => onView("artifacts")}>
        Open artifacts
      </button>
      <button type="button" onClick={() => onView("capabilities")}>
        Open capabilities
      </button>
    </div>
  ),
}));
vi.mock("./studio-account-menu", () => ({ StudioAccountMenu: () => null }));
vi.mock("./studio-artifacts", () => ({
  StudioArtifacts: () => <div>Artifacts view</div>,
  chartSpecWithRows: () => "{}",
}));
vi.mock("./studio-capabilities", () => ({
  StudioCapabilities: ({
    onCreateWithChat,
  }: {
    onCreateWithChat: () => void;
  }) => <button onClick={onCreateWithChat}>Create with chat</button>,
}));

function renderStudio() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <StudioApp />
    </QueryClientProvider>,
  );
}

describe("StudioApp thread routing", () => {
  beforeEach(() => {
    mocks.navigate.mockReset();
    mocks.search = { agent: "a1", thread: "thread-1" };
  });

  it("keeps a selected thread instead of removing it again", async () => {
    const screen = await renderStudio();

    await expect
      .element(screen.getByText("Revenue Analyst"))
      .toBeInTheDocument();
    await expect.element(screen.getByText("Threads: 1")).toBeInTheDocument();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("keeps Auto available while the specialist catalog is temporarily unavailable", async () => {
    mocks.search = {};
    const listStudio = vi.mocked(agentsApi.listStudio);
    const listThreads = vi.mocked(agentsApi.listThreads);
    const normalStudio = listStudio.getMockImplementation();
    const normalThreads = listThreads.getMockImplementation();
    listStudio.mockRejectedValue(new Error("503"));
    listThreads.mockResolvedValue({ threads: [], count: 0 });
    try {
      const screen = await renderStudio();

      await expect.element(screen.getByText("Auto", { exact: true })).toBeVisible();
      await expect.element(screen.getByText("No agent")).not.toBeInTheDocument();
      await expect.element(screen.getByRole("status")).toHaveTextContent(
        "Specialists are temporarily unavailable.",
      );
      const calls = listStudio.mock.calls.length;
      await userEvent.click(screen.getByRole("button", { name: "Retry specialists" }));
      await expect.poll(() => listStudio.mock.calls.length).toBeGreaterThan(calls);
    } finally {
      if (normalStudio) listStudio.mockImplementation(normalStudio);
      if (normalThreads) listThreads.mockImplementation(normalThreads);
    }
  });

  it("opens the newest thread once when no thread is selected", async () => {
    mocks.search = { agent: "a1" };
    await renderStudio();

    await expect.poll(() => mocks.navigate.mock.calls.length).toBe(1);
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: "/studio",
      search: { agent: "a1", thread: "thread-1" },
      replace: true,
    });

    await new Promise((resolve) => window.setTimeout(resolve, 50));
    expect(mocks.navigate).toHaveBeenCalledTimes(1);
  });

  it("starts a fresh chat from the header without reopening history", async () => {
    const screen = await renderStudio();
    await expect
      .element(screen.getByText("Revenue Analyst"))
      .toBeInTheDocument();
    mocks.navigate.mockImplementation((options) => {
      mocks.search = options.search;
    });

    await userEvent.click(
      screen.getByRole("button", { name: "Header new chat" }),
    );

    expect(mocks.navigate).toHaveBeenCalledTimes(1);
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: "/studio",
      search: { agent: "a1" },
      replace: true,
    });
    // Force a parent render after the URL lost its thread. If freshChat were
    // reset to false, the auto-open effect would navigate straight back to the
    // newest history item and this count would become two.
    await userEvent.click(
      screen.getByRole("button", { name: "Open artifacts" }),
    );
    await new Promise((resolve) => window.setTimeout(resolve, 50));
    expect(mocks.navigate).toHaveBeenCalledTimes(2);
    expect(mocks.navigate).toHaveBeenLastCalledWith({
      to: "/studio",
      search: { agent: "a1", thread: undefined, view: "artifacts" },
      replace: true,
    });
  });

  it("starts skill authoring in a fresh chat from capabilities", async () => {
    const screen = await renderStudio();
    await expect
      .element(screen.getByText("Revenue Analyst"))
      .toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Open capabilities" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create with chat" }),
    );
    await expect
      .element(screen.getByText("/create-skill-with-chat"))
      .toBeInTheDocument();
    expect(mocks.navigate).toHaveBeenLastCalledWith({
      to: "/studio",
      search: { agent: "nova-skill-author" },
      replace: true,
    });
  });

  it("restores an embedded author conversation without adding it to user agents", async () => {
    mocks.search = { agent: "nova-skill-author", thread: "draft-1" };
    const screen = await renderStudio();
    await expect.element(screen.getByText("Nova Studio")).toBeVisible();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("keeps Studio focused on the agent conversation without the Nove panel", async () => {
    const screen = await renderStudio();
    await expect.element(screen.getByRole("main", { name: "Nova Studio chat" })).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Ask Nove" })).not.toBeInTheDocument();
    await expect.element(screen.getByRole("heading", { name: "Nova Studio" })).not.toBeInTheDocument();
  });

  it("returns to chat and starts fresh from the sidebar action", async () => {
    const screen = await renderStudio();
    await userEvent.click(
      screen.getByRole("button", { name: "Open artifacts" }),
    );
    await expect
      .element(screen.getByText("Artifacts view"))
      .toBeInTheDocument();

    mocks.navigate.mockClear();
    await userEvent.click(
      screen.getByRole("button", { name: "Sidebar new chat" }),
    );

    await expect
      .element(screen.getByText("Revenue Analyst"))
      .toBeInTheDocument();
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: "/studio",
      search: { agent: "a1" },
      replace: true,
    });
  });

  it("does not auto-open history while a sample question creates its thread", async () => {
    const screen = await renderStudio();
    mocks.navigate.mockImplementation((options) => {
      if (!options.search.thread) mocks.search = options.search;
    });

    await userEvent.click(
      screen.getByRole("button", { name: "Sidebar new chat" }),
    );
    mocks.navigate.mockClear();
    mocks.navigate.mockImplementation(() => {});

    await userEvent.click(
      screen.getByRole("button", { name: "Sample creates thread" }),
    );
    await new Promise((resolve) => window.setTimeout(resolve, 50));

    expect(mocks.navigate).toHaveBeenCalledTimes(1);
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: "/studio",
      search: { agent: "a1", thread: "thread-2" },
      replace: true,
    });
  });
});
