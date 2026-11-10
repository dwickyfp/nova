import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { agentsApi, studioApi, type AgentThread } from "@/features/agents/api";
import { StudioApp } from "./index";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  listeners: new Set<() => void>(),
  pathname: '/studio',
  search: { agent: "a1", thread: "thread-1" } as {
    agent?: string;
    thread?: string;
    view?: "chat" | "artifacts" | "capabilities" | "news";
    desk?: "alerts";
  },
}));

vi.mock("@tanstack/react-router", async importOriginal => {
  const original = await importOriginal<typeof import('@tanstack/react-router')>()
  const { useSyncExternalStore } = await import('react')
  const subscribe = (listener: () => void) => {
    mocks.listeners.add(listener)
    return () => { mocks.listeners.delete(listener) }
  }
  return {
    ...original,
    useNavigate: () => (options: { to: string; search?: typeof mocks.search }) => {
      const result = mocks.navigate(options)
      mocks.search = options.search ?? {}
      mocks.pathname = options.to
      mocks.listeners.forEach(listener => listener())
      return result
    },
    useSearch: () => useSyncExternalStore(subscribe, () => mocks.search),
    useRouterState: ({ select }: { select: (state: { location: { pathname: string } }) => unknown }) => select({ location: { pathname: useSyncExternalStore(subscribe, () => mocks.pathname) } }),
    Outlet: () => <div>Report route content</div>,
  }
});

vi.mock("@/features/agents/api", () => ({
  agentsApi: {
    listStudio: vi.fn(async () => ({
      agents: [
        {
          agent_id: "a1",
          name: "Revenue Analyst",
          database_name: "sales",
        },
        { agent_id: "a2", name: "Support Agent" },
      ],
    })),
    listThreads: vi.fn(async () => ({
      threads: [{ thread_id: "thread-1", agent_id: "a1", title: "Revenue" }],
    })),
    deleteThread: vi.fn(async () => undefined),
    renameThread: vi.fn(async () => ({})),
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
    onSelectAgent,
    noAgents,
  }: {
    agent: { name: string } | null;
    onThreadChange: (id: string | null) => void;
    initialPrompt?: string;
    onSelectAgent: (id: string) => void;
    noAgents?: boolean;
  }) => (
    <div>
      <span>{agent?.name ?? "No agent"}</span>
      {noAgents ? <span>Empty agent catalog</span> : null}
      <span>{initialPrompt}</span>
      <button onClick={() => onSelectAgent("a2")}>Choose Support</button>
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
    onOpenThread,
    onRenameThread,
    onDeleteThread,
  }: {
    threads: AgentThread[];
    onNewChat: () => void;
    onView: (view: "artifacts" | "capabilities") => void;
    onOpenThread: (id: string) => void;
    onRenameThread: (id: string, title: string) => void;
    onDeleteThread: (id: string) => void;
  }) => (
    <div>
      <span>Threads: {threads.length}</span>
      {threads.map(thread => <div key={thread.thread_id}>
        <button onClick={() => onOpenThread(thread.thread_id)}>Open {thread.title}</button>
        <button onClick={() => onRenameThread(thread.thread_id, "Updated")}>Rename {thread.title}</button>
        <button onClick={() => onDeleteThread(thread.thread_id)}>Delete {thread.title}</button>
      </div>)}
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
vi.mock("./studio-intelligence", () => ({
  StudioIntelligence: ({ onFollowUp }: { onFollowUp: (prompt: string) => void }) => (
    <button type="button" onClick={() => onFollowUp("Why did refunds rise?")}>Follow up alert</button>
  ),
}));
vi.mock("./news/newspaper-page", () => ({
  NewspaperPage: ({ investigateAgentId }: { investigateAgentId?: string | null }) => (
    <span>Investigates with {investigateAgentId === undefined ? "loading" : investigateAgentId ?? "no agent"}</span>
  ),
}));
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
    vi.mocked(agentsApi.listThreads).mockClear();
    vi.mocked(agentsApi.deleteThread).mockClear();
    vi.mocked(agentsApi.renameThread).mockClear();
    mocks.search = { agent: "a1", thread: "thread-1" };
    mocks.pathname = '/studio';
    window.localStorage.removeItem("nova.studio.lastAgent");
  });

  const mixedHistory = {
    count: 3,
    threads: [
      { thread_id: "support-1", agent_id: "a2", title: "Support" },
      { thread_id: "thread-1", agent_id: "a1", title: "Revenue" },
      { thread_id: "thread-0", agent_id: "a1", title: "Older revenue" },
    ] as AgentThread[],
  };

  it("opens mixed history using each conversation's agent", async () => {
    vi.mocked(agentsApi.listThreads).mockResolvedValueOnce(mixedHistory);
    const screen = await renderStudio();
    await expect.element(screen.getByText("Threads: 3")).toBeVisible();
    expect(agentsApi.listThreads).toHaveBeenCalledWith();
    await userEvent.click(screen.getByRole("button", { name: "Open Support" }));
    expect(mocks.navigate).toHaveBeenLastCalledWith({ to: "/studio", search: { agent: "a2", thread: "support-1" }, replace: true });
    await userEvent.click(screen.getByRole("button", { name: "Open Older revenue" }));
    expect(mocks.navigate).toHaveBeenLastCalledWith({ to: "/studio", search: { agent: "a1", thread: "thread-0" }, replace: true });
  });

  it("renames and deletes another agent's history without using the selected agent", async () => {
    vi.mocked(agentsApi.listThreads).mockResolvedValueOnce(mixedHistory).mockResolvedValueOnce(mixedHistory);
    const screen = await renderStudio();
    await userEvent.click(screen.getByRole("button", { name: "Rename Support" }));
    expect(agentsApi.renameThread).toHaveBeenCalledWith("a2", "support-1", "Updated");
    await userEvent.click(screen.getByRole("button", { name: "Delete Support" }));
    expect(agentsApi.deleteThread).toHaveBeenCalledWith("a2", "support-1");
  });

  it("opens the newest conversation with its owner when no agent is requested", async () => {
    mocks.search = {};
    vi.mocked(agentsApi.listThreads).mockResolvedValueOnce(mixedHistory);
    await renderStudio();
    await expect.poll(() => mocks.navigate.mock.calls.length).toBe(1);
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/studio", search: { agent: "a2", thread: "support-1" }, replace: true });
  });

  it("restores only the requested agent's newest conversation", async () => {
    mocks.search = { agent: "a1" };
    vi.mocked(agentsApi.listThreads).mockResolvedValueOnce(mixedHistory);
    await renderStudio();
    await expect.poll(() => mocks.navigate.mock.calls.length).toBe(1);
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/studio", search: { agent: "a1", thread: "thread-1" }, replace: true });
  });

  it("keeps the unified history when choosing a different agent for a new chat", async () => {
    vi.mocked(agentsApi.listThreads).mockResolvedValueOnce(mixedHistory);
    mocks.navigate.mockImplementation(options => { mocks.search = options.search; });
    const screen = await renderStudio();
    await expect.element(screen.getByText("Threads: 3")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Choose Support" }));
    await expect.element(screen.getByText("Threads: 3")).toBeVisible();
    expect(agentsApi.listThreads).toHaveBeenCalledTimes(1);
    expect(mocks.navigate).toHaveBeenLastCalledWith({ to: "/studio", search: { agent: "a2" }, replace: true });
  });

  it("keeps a selected thread instead of removing it again", async () => {
    const screen = await renderStudio();

    await expect
      .element(screen.getByText("Revenue Analyst"))
      .toBeInTheDocument();
    await expect.element(screen.getByText("Threads: 1")).toBeInTheDocument();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("reports an agent catalog that cannot load and retries it", async () => {
    mocks.search = {};
    const listStudio = vi.mocked(agentsApi.listStudio);
    const listThreads = vi.mocked(agentsApi.listThreads);
    const normalStudio = listStudio.getMockImplementation();
    const normalThreads = listThreads.getMockImplementation();
    listStudio.mockRejectedValue(new Error("503"));
    listThreads.mockResolvedValue({ threads: [], count: 0 });
    try {
      const screen = await renderStudio();

      await expect.element(screen.getByText("No agent")).toBeVisible();
      // A failed load is not the same as having no agents.
      await expect.element(screen.getByText("Empty agent catalog")).not.toBeInTheDocument();
      await expect.element(screen.getByRole("status")).toHaveTextContent(
        "Your agents could not load.",
      );
      const calls = listStudio.mock.calls.length;
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      await expect.poll(() => listStudio.mock.calls.length).toBeGreaterThan(calls);
    } finally {
      if (normalStudio) listStudio.mockImplementation(normalStudio);
      if (normalThreads) listThreads.mockImplementation(normalThreads);
    }
  });

  it("keeps agents awaiting access verification out of the Studio banner and selection", async () => {
    mocks.search = {};
    const listStudio = vi.mocked(agentsApi.listStudio);
    const normalStudio = listStudio.getMockImplementation();
    listStudio.mockResolvedValue({
      agents: [{ agent_id: "a3", name: "Field Ops" }] as Awaited<ReturnType<typeof agentsApi.listStudio>>["agents"],
      count: 1,
      needs_access: [{ agent_id: "sales", name: "Sales Agent" }],
    });
    try {
      const screen = await renderStudio();

      await expect.element(screen.getByText("Field Ops", { exact: true })).toBeVisible();
      await expect.element(screen.getByText(/not available here yet/)).not.toBeInTheDocument();
      await expect.element(screen.getByRole("button", { name: "Open access settings" })).not.toBeInTheDocument();
      await expect.element(screen.getByText("Sales Agent")).not.toBeInTheDocument();
    } finally {
      if (normalStudio) listStudio.mockImplementation(normalStudio);
    }
  });

  it("asks for a role instead of reporting load failures when none is active", async () => {
    mocks.search = {};
    const settings = vi.mocked(studioApi.settings);
    const listStudio = vi.mocked(agentsApi.listStudio);
    const normalSettings = settings.getMockImplementation();
    const normalStudio = listStudio.getMockImplementation();
    settings.mockResolvedValue({
      identity: { username: "ana", active_role: null, roles: ["ANALYST", "FINANCE"] },
      preferences: { preferred_name: null },
    } as unknown as Awaited<ReturnType<typeof studioApi.settings>>);
    listStudio.mockRejectedValue(new Error("403"));
    try {
      const screen = await renderStudio();

      await expect.element(screen.getByRole("status")).toHaveTextContent(
        "Choose a role to see your data and conversations.",
      );
      await expect.element(screen.getByRole("button", { name: "ANALYST" })).toBeVisible();
      await expect.element(screen.getByRole("button", { name: "FINANCE" })).toBeVisible();
      await expect
        .element(screen.getByText("Your agents could not load."))
        .not.toBeInTheDocument();
    } finally {
      if (normalSettings) settings.mockImplementation(normalSettings);
      if (normalStudio) listStudio.mockImplementation(normalStudio);
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

  it("does not replace an explicit agent link with another agent's latest thread", async () => {
    mocks.search = { agent: "a2" };
    await renderStudio();
    await new Promise((resolve) => window.setTimeout(resolve, 50));
    expect(mocks.navigate).not.toHaveBeenCalled();
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

  it("keeps Studio focused on the agent conversation without the Neva panel", async () => {
    const screen = await renderStudio();
    await expect.element(screen.getByRole("main", { name: "Nova Studio chat" })).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Ask Neva" })).not.toBeInTheDocument();
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

  describe("default agent", () => {
    const emptyHistory = { threads: [] as AgentThread[], count: 0 };

    it("opens the agent last used in this browser and starts new chats with it", async () => {
      mocks.search = {};
      window.localStorage.setItem("nova.studio.lastAgent", "a2");
      vi.mocked(agentsApi.listThreads).mockResolvedValueOnce(emptyHistory);
      const screen = await renderStudio();
      await expect.element(screen.getByText("Support Agent")).toBeVisible();
      await userEvent.click(screen.getByRole("button", { name: "Sidebar new chat" }));
      expect(mocks.navigate).toHaveBeenLastCalledWith({ to: "/studio", search: { agent: "a2" }, replace: true });
    });

    it("falls back to the first agent when the remembered one is gone", async () => {
      mocks.search = {};
      window.localStorage.setItem("nova.studio.lastAgent", "deleted-agent");
      vi.mocked(agentsApi.listThreads).mockResolvedValueOnce(emptyHistory);
      const screen = await renderStudio();
      await expect.element(screen.getByText("Revenue Analyst")).toBeVisible();
      await userEvent.click(screen.getByRole("button", { name: "Sidebar new chat" }));
      expect(mocks.navigate).toHaveBeenLastCalledWith({ to: "/studio", search: { agent: "a1" }, replace: true });
    });

    it("opens the default agent for a retired agent link", async () => {
      for (const retired of ["__smart__", "__auto__"]) {
        mocks.search = { agent: retired };
        window.localStorage.setItem("nova.studio.lastAgent", "a2");
        vi.mocked(agentsApi.listThreads).mockResolvedValueOnce(emptyHistory);
        const screen = await renderStudio();
        await expect.element(screen.getByText("Support Agent")).toBeVisible();
        await screen.unmount();
      }
    });

    it("remembers the agent the user chooses", async () => {
      const screen = await renderStudio();
      await userEvent.click(screen.getByRole("button", { name: "Choose Support" }));
      expect(window.localStorage.getItem("nova.studio.lastAgent")).toBe("a2");
    });

    it("tells the chat when the user has no agent", async () => {
      mocks.search = {};
      const listStudio = vi.mocked(agentsApi.listStudio);
      const normalStudio = listStudio.getMockImplementation();
      listStudio.mockResolvedValue({ agents: [], count: 0 });
      vi.mocked(agentsApi.listThreads).mockResolvedValueOnce(emptyHistory);
      try {
        const screen = await renderStudio();
        await expect.element(screen.getByText("Empty agent catalog")).toBeVisible();
        await expect.element(screen.getByText("No agent")).toBeVisible();
      } finally {
        if (normalStudio) listStudio.mockImplementation(normalStudio);
      }
    });

    it("follows up a News alert with the default agent", async () => {
      mocks.search = { view: "news", desk: "alerts" };
      window.localStorage.setItem("nova.studio.lastAgent", "a2");
      const screen = await renderStudio();
      await userEvent.click(screen.getByRole("button", { name: "Follow up alert" }));
      expect(mocks.navigate).toHaveBeenLastCalledWith({ to: "/studio", search: { agent: "a2" }, replace: true });
      await expect.element(screen.getByText("Why did refunds rise?")).toBeVisible();
    });

    it("hands News the default agent to investigate with, or none", async () => {
      mocks.search = { view: "news" };
      window.localStorage.setItem("nova.studio.lastAgent", "a2");
      const screen = await renderStudio();
      await expect.element(screen.getByText("Investigates with a2")).toBeVisible();
      await screen.unmount();

      const listStudio = vi.mocked(agentsApi.listStudio);
      const normalStudio = listStudio.getMockImplementation();
      listStudio.mockResolvedValue({ agents: [], count: 0 });
      try {
        mocks.search = { view: "news" };
        const empty = await renderStudio();
        await expect.element(empty.getByText("Investigates with no agent")).toBeVisible();
      } finally {
        if (normalStudio) listStudio.mockImplementation(normalStudio);
      }
    });
  });
});
