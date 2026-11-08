import { useCallback, useEffect, useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { getCookie, setCookie } from "@/lib/cookies";
import { useIsMobile } from "@/hooks/use-mobile";
import { agentsApi, studioApi } from "@/features/agents/api";
import { api } from "@/lib/api-client";
import { StudioChat, type StudioHandoff } from "./studio-chat";
import { StudioSidebar, type StudioView } from "./studio-sidebar";
import { StudioAccountMenu } from "./studio-account-menu";
import { StudioArtifacts } from "./studio-artifacts";
import { StudioCapabilities } from "./studio-capabilities";
import { StudioShared } from "./studio-shared";
import { StudioIntelligence } from "./studio-intelligence";
import { NewspaperPage } from "./news/newspaper-page";
import { ShareDialog } from "./share-dialog";
import { flushUiStates } from "./intelligent-ui/state";
import { CREATE_SKILL_COMMAND, SKILL_AUTHOR_ID } from "./skill-document";
import { pickDefaultAgent, rememberLastAgent } from "./default-agent";
import { DatavizWorkspace } from "./dataviz/dataviz-workspace";
import { StudioTabs, type StudioTab } from "./studio-tabs";

export function StudioApp() {
  return <StudioAppContent />;
}

function StudioAppContent() {
  const search = useSearch({ from: "/studio" });
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const agentsQuery = useQuery({
    queryKey: ["studio", "agents", "list"],
    queryFn: () => agentsApi.listStudio(),
    retry: 1,
    retryDelay: 500,
    refetchInterval: (query) => query.state.status === "error" ? 5000 : false,
  });
  const agents = useMemo(() => agentsQuery.data?.agents ?? [], [agentsQuery.data]);
  const authorQuery = useQuery({
    queryKey: ["studio", "skill-author"],
    queryFn: studioApi.skillAuthor,
    enabled: search.agent === SKILL_AUTHOR_ID,
  });

  const settingsQuery = useQuery({
    queryKey: ["studio", "settings"],
    queryFn: () => studioApi.settings(),
  });

  // Without an active role nothing loads; say so instead of showing load errors.
  const identity = settingsQuery.data?.identity;
  const roleChoices = identity?.roles ?? [];
  const needsRole = Boolean(identity && !identity.active_role && roleChoices.length);
  const chooseRole = async (role: string) => {
    await api.post("/auth/switch-role", { role });
    await queryClient.invalidateQueries({ queryKey: ["studio", "settings"] });
    window.location.reload();
  };

  const requestedAgentId = search.agent;
  // Without a valid agent in the URL (none, removed, or a retired id), open
  // the agent last used in this browser, then the first one in the catalog.
  const defaultAgentId = pickDefaultAgent(agents);
  const agentId = requestedAgentId && (
    requestedAgentId === SKILL_AUTHOR_ID || agents.some((agent) => agent.agent_id === requestedAgentId)
  ) ? requestedAgentId : defaultAgentId;
  const view: StudioView = search.view ?? "chat";
  // Agent and Dataviz are top-level workspaces, separate from the sidebar view.
  const tab: StudioTab = search.tab ?? "agent";
  // Dataviz mounts on first visit and then stays mounted, so its Superset
  // frame (and any unsaved edit in it) survives a visit to the Agent tab.
  const [datavizMounted, setDatavizMounted] = useState(tab === "dataviz");
  useEffect(() => {
    if (tab === "dataviz") setDatavizMounted(true);
  }, [tab]);
  const selectTab = (next: StudioTab) => {
    void navigate({
      to: "/studio",
      search: { ...search, tab: next === "agent" ? undefined : next },
      replace: true,
    });
  };
  const followBiPath = useCallback(
    (bi: string) => {
      void navigate({ from: "/studio", to: "/studio", search: (prev) => ({ ...prev, bi }), replace: true });
    },
    [navigate],
  );
  // The open thread lives in the URL, not in component state: a refresh has to
  // come back to the same conversation, and losing it made the transcript (and
  // its thinking) look like it had vanished.
  const activeThreadId = search.thread ?? null;
  // Bumped to ask the chat for a fresh conversation; the chat owns the
  // transcript, so this is a request, not the state itself.
  const [newChatNonce, setNewChatNonce] = useState(0);
  // Set when the user explicitly starts a new chat. It stops the "open the
  // newest conversation" effect below from immediately pulling them back into
  // one, which is the opposite of what they asked for.
  const [freshChat, setFreshChat] = useState(false);
  const [initialPrompt, setInitialPrompt] = useState("");
  // A News story handed to Studio: its own thread, Mission and prompt.
  const [handoff, setHandoff] = useState<StudioHandoff | null>(null);
  const [sharing, setSharing] = useState<{ id: string; title: string } | null>(null);
  // Shared with Nova's main sidebar, so the rail remembers its state whichever
  // surface set it last.
  const [sidebarOpen, setSidebarOpen] = useState(
    () => getCookie("sidebar_state") !== "false",
  );
  const isMobile = useIsMobile();
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const visibleSidebarOpen = isMobile ? mobileSidebarOpen : sidebarOpen;

  const toggleSidebar = () => {
    if (isMobile) {
      setMobileSidebarOpen((open) => !open);
      return;
    }
    setSidebarOpen((prev) => {
      setCookie("sidebar_state", prev ? "false" : "true");
      return !prev;
    });
  };

  const agent = useMemo(
    () =>
      agentId === SKILL_AUTHOR_ID
        ? (authorQuery.data ?? null)
        : (agents.find((a) => a.agent_id === agentId) ?? null),
    [agents, agentId, authorQuery.data],
  );

  const threadsQuery = useInfiniteQuery({
    queryKey: ["agents", "threads", "all", settingsQuery.data?.identity.username, settingsQuery.data?.identity.active_role],
    queryFn: ({ pageParam }) => pageParam ? agentsApi.listThreads(undefined, pageParam) : agentsApi.listThreads(),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: settingsQuery.isSuccess,
  });
  const threads = [...new Map(threadsQuery.data?.pages.flatMap((page) => page.threads).map((thread) => [thread.thread_id, thread]) ?? []).values()];

  const threadAgentId = (id: string) =>
    threads.find((thread) => thread.thread_id === id)?.agent_id;

  // Restore the newest conversation with its original agent.
  useEffect(() => {
    if (
      view !== "chat" ||
      search.thread ||
      !threadsQuery.isSuccess ||
      freshChat
    )
      return;
    const newest = requestedAgentId
      ? threads.find((thread) => thread.agent_id === requestedAgentId)
      : threads[0];
    if (!newest?.agent_id) return;
    // The workspace tab and the Dataviz page stay: a link that opens
    // Dataviz must not be turned into the Agent tab.
    navigate({
      to: "/studio",
      search: {
        agent: newest.agent_id,
        thread: newest.thread_id,
        tab: search.tab,
        bi: search.bi,
      },
      replace: true,
    });
  }, [
    freshChat,
    navigate,
    search.thread,
    search.tab,
    search.bi,
    requestedAgentId,
    threads,
    threadsQuery.isSuccess,
    view,
  ]);

  const selectAgent = async (id: string) => {
    try { await flushUiStates(); } catch (error) { toast.error((error as Error).message); return false; }

    setMobileSidebarOpen(false);
    setInitialPrompt("");
    setNewChatNonce(0);

    rememberLastAgent(id);
    // A new agent starts a fresh conversation, so the thread is dropped.
    setFreshChat(true);
    await navigate({ to: "/studio", search: { agent: id }, replace: true });
    return true;
  };

  const selectThread = async (id: string) => {
    try { await flushUiStates(); } catch (error) { toast.error((error as Error).message); return; }

    const owner = threadAgentId(id);
    if (!owner) return;
    setMobileSidebarOpen(false);
    setInitialPrompt("");

    navigate({
      to: "/studio",
      search: { agent: owner, thread: id },
      replace: true,
    });
  };

  const startNewChat = async () => {
    try { await flushUiStates(); } catch (error) { toast.error((error as Error).message); return; }

    setMobileSidebarOpen(false);
    setInitialPrompt("");

    setFreshChat(true);
    setNewChatNonce((n) => n + 1);
    navigate({
      to: "/studio",
      search: { agent: pickDefaultAgent(agents) ?? undefined },
      replace: true,
    });
  };

  const removeThread = useMutation({
    mutationFn: (threadId: string) => {
      const owner = threadAgentId(threadId);
      if (!owner) throw new Error("Conversation is no longer available. Refresh history.");
      return agentsApi.deleteThread(owner, threadId);
    },
    onSuccess: (_result, threadId) => {
      queryClient.invalidateQueries({
        queryKey: ["agents", "threads"],
      });
      // Deleting the open conversation leaves the URL pointing at a thread that
      // no longer exists. Close it rather than render a dead transcript.
      if (threadId === activeThreadId) startNewChat();
      toast.success("Conversation deleted");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const createSkillChat = useMutation({
    mutationFn: async () => {
      const author = await studioApi.skillAuthor();
      queryClient.setQueryData(["studio", "skill-author"], author);
      return author;
    },
    onSuccess: async (selected) => {
      if (!await selectAgent(selected.agent_id)) return;
      setNewChatNonce((n) => n + 1);
      setInitialPrompt(`${CREATE_SKILL_COMMAND} `);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const renameThread = useMutation({
    mutationFn: ({ threadId, title }: { threadId: string; title: string }) => {
      const owner = threadAgentId(threadId);
      if (!owner) throw new Error("Conversation is no longer available. Refresh history.");
      return agentsApi.renameThread(owner, threadId, title);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agents", "threads"],
      });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div
      className="relative flex h-dvh w-full overflow-hidden bg-background text-foreground"
      onKeyDown={(event) => {
        if (event.key === "Escape") setMobileSidebarOpen(false);
      }}
    >
      {isMobile && mobileSidebarOpen ? (
        <button
          aria-label="Close navigation"
          className="absolute inset-0 z-30 bg-background/80"
          onClick={() => setMobileSidebarOpen(false)}
        />
      ) : null}
      <div
        data-mobile-navigation={isMobile && mobileSidebarOpen}
        className={
          isMobile && mobileSidebarOpen
            ? "absolute inset-y-0 left-0 z-40"
            : "shrink-0"
        }
      >
        <StudioSidebar
          newsEnabled={settingsQuery.data?.identity.news_enabled === true}
          view={view}
          onView={async (nextView) => {
            try { await flushUiStates(); } catch (error) { toast.error((error as Error).message); return; }

            setMobileSidebarOpen(false);
            navigate({
              to: "/studio",
              search: {
                agent: agentId ?? undefined,
                thread: activeThreadId ?? undefined,
                view: nextView,
              },
              replace: true,
            });
          }}
          threads={threads}
          activeThreadId={activeThreadId}
          onOpenThread={selectThread}
          onNewChat={startNewChat}
          onDeleteThread={(threadId) => removeThread.mutate(threadId)}
          onRenameThread={(threadId, title) =>
            renameThread.mutate({ threadId, title })
          }
          onShareThread={(thread) => setSharing({ id: thread.thread_id, title: thread.title })}
          threadsLoading={threadsQuery.isLoading}
          threadsError={threadsQuery.isError && !needsRole}
          onRetryThreads={() => void threadsQuery.refetch()}
          hasMoreThreads={threadsQuery.hasNextPage}
          loadingMoreThreads={threadsQuery.isFetchingNextPage}
          onLoadMoreThreads={() => void threadsQuery.fetchNextPage()}
          open={visibleSidebarOpen}
          onToggle={toggleSidebar}
          footer={
            <StudioAccountMenu
              identity={settingsQuery.data?.identity}
              memoryAgentId={agent && agent.agent_id !== SKILL_AUTHOR_ID ? agent.agent_id : null}
              onIdentityChange={() =>
                queryClient.invalidateQueries({
                  queryKey: ["studio", "settings"],
                })
              }
              collapsed={!visibleSidebarOpen}
            />
          }
        />
      </div>

      <main aria-label={tab === "dataviz" ? "Nova Studio Dataviz" : view === "chat" ? "Nova Studio chat" : view} className="shell-pane flex min-h-0 min-w-0 flex-1 flex-col bg-background">
        <StudioTabs tab={tab} onSelect={selectTab} />
        {needsRole ? (
          <div role="status" className="flex shrink-0 flex-wrap items-center justify-center gap-x-2 gap-y-1 border-b border-border px-4 py-2 text-xs text-muted-foreground">
            <span>Choose a role to see your data and conversations.</span>
            {roleChoices.slice(0, 4).map((role) => (
              <Button key={role} type="button" variant="link" size="sm" className="h-auto px-0 text-xs" onClick={() => void chooseRole(role)}>
                {role}
              </Button>
            ))}
            {roleChoices.length > 4 ? <span>More roles are in the account menu.</span> : null}
          </div>
        ) : agentsQuery.isError ? (
          <div role="status" className="flex shrink-0 flex-wrap items-center justify-center gap-x-2 border-b border-border px-4 py-2 text-xs text-muted-foreground">
            <span>Your agents could not load.</span>
            <Button type="button" variant="link" size="sm" className="h-auto px-0 text-xs" onClick={() => void agentsQuery.refetch()}>
              Retry
            </Button>
          </div>
        ) : null}
        {datavizMounted ? (
          <div className={tab === "dataviz" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
            <DatavizWorkspace
              path={search.bi}
              onPathChange={followBiPath}
              onContinueInAgent={(prompt) => {
                setInitialPrompt(prompt);
                setFreshChat(true);
                setNewChatNonce((value) => value + 1);
                void navigate({
                  to: "/studio",
                  search: { agent: agentId ?? undefined },
                  replace: true,
                });
              }}
              currentRole={settingsQuery.data?.identity.active_role ?? null}
              displayName={
                settingsQuery.data?.preferences.preferred_name ||
                settingsQuery.data?.identity.username
              }
              visible={tab === "dataviz"}
            />
          </div>
        ) : null}
        <div className={tab === "agent" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
        {view === "chat" ? (
          <StudioChat
            agent={agent}
            agents={agents}
            onSelectAgent={selectAgent}
            currentRole={settingsQuery.data?.identity.active_role ?? null}
            displayName={
              settingsQuery.data?.preferences.preferred_name ||
              settingsQuery.data?.identity.username
            }
            activeThreadId={activeThreadId}
            onThreadChange={(id) => {
              // Keep the guard enabled until navigation has committed the new
              // thread id. Clearing it here briefly exposes an empty URL to the
              // auto-open effect, which can replace the new chat with history.
              if (id === null) setFreshChat(true);
              // A thread created by the first message joins the URL, so a
              // refresh before the next message still returns here.
              navigate({
                to: "/studio",
                search: id
                  ? { agent: agentId ?? undefined, thread: id }
                  : { agent: agentId ?? undefined },
                replace: true,
              });
            }}
            newChatNonce={newChatNonce}
            initialPrompt={initialPrompt}
            handoff={handoff}
            noAgents={agentsQuery.isSuccess && agents.length === 0}
          />
        ) : view === "news" && !search.item && search.desk !== "alerts" ? (
          <NewspaperPage
            story={search.story}
            edition={search.edition}
            onEdition={(edition) => navigate({ to: "/studio", search: { view: "news", edition }, replace: true })}
            onOpen={(story) => navigate({ to: "/studio", search: { view: "news", story, edition: search.edition }, replace: true })}
            onAlerts={() => navigate({ to: "/studio", search: { view: "news", desk: "alerts" }, replace: true })}
            investigateAgentId={agentsQuery.isSuccess ? defaultAgentId : undefined}
            onInvestigate={(next) => {
              setHandoff({ threadId: next.thread_id, missionId: next.mission_id, prompt: next.prompt });
              setFreshChat(false);
              navigate({
                to: "/studio",
                search: { agent: next.agent_id, thread: next.thread_id },
                replace: true,
              });
            }} />
        ) : view === "news" ? (
          <StudioIntelligence item={search.item}
            onOpen={(item) => navigate({ to: "/studio", search: { view, item, desk: "alerts" }, replace: true })}
            onFollowUp={(prompt) => {
              setInitialPrompt(prompt);
              setFreshChat(true);
              setNewChatNonce((value) => value + 1);

              navigate({ to: "/studio", search: { agent: pickDefaultAgent(agents) ?? undefined }, replace: true });
            }} />
        ) : view === "artifacts" || view === "dashboards" ? (
          <StudioArtifacts onSelectAgent={selectAgent} />
        ) : view === "shared" ? (
          <StudioShared />
        ) : (
          <StudioCapabilities
            onCreateWithChat={() => {
              if (!createSkillChat.isPending) createSkillChat.mutate();
            }}
            creatingChat={createSkillChat.isPending}
          />
        )}
        </div>
      </main>
      <ShareDialog
        objectType="thread"
        objectId={sharing?.id ?? null}
        title={sharing?.title || "Conversation"}
        onClose={() => setSharing(null)}
      />
    </div>
  );
}
