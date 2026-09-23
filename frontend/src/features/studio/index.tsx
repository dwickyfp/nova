import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { toast } from "sonner";
import { getCookie, setCookie } from "@/lib/cookies";
import { useIsMobile } from "@/hooks/use-mobile";
import { agentsApi, studioApi } from "@/features/agents/api";
import { StudioChat } from "./studio-chat";
import { StudioSidebar, type StudioView } from "./studio-sidebar";
import { StudioAccountMenu } from "./studio-account-menu";
import { StudioArtifacts } from "./studio-artifacts";
import { StudioCapabilities } from "./studio-capabilities";
import { StudioDashboards } from "./studio-dashboards";
import { CREATE_SKILL_COMMAND, SKILL_AUTHOR_ID } from "./skill-document";

/**
 * Nova Studio app shell — a full-page standalone surface.
 *
 * It deliberately does **not** use Nova's layout: no global sidebar, no header
 * bar. It renders its own left rail and content area, sized to the viewport, so
 * it reads as a separate product the way Snowflake CoWork does. The only shared
 * chrome is the toast host, mounted by the root route.
 */
export function StudioApp() {
  const search = useSearch({ from: "/studio" });
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const agentsQuery = useQuery({
    queryKey: ["studio", "agents", "list"],
    queryFn: () => agentsApi.listStudio(),
  });
  const agents = agentsQuery.data?.agents ?? [];
  const authorQuery = useQuery({
    queryKey: ["studio", "skill-author"],
    queryFn: studioApi.skillAuthor,
    enabled: search.agent === SKILL_AUTHOR_ID,
  });

  const settingsQuery = useQuery({
    queryKey: ["studio", "settings"],
    queryFn: () => studioApi.settings(),
  });

  const [agentId, setAgentId] = useState<string | null>(null);
  const [view, setView] = useState<StudioView>(search.view ?? "chat");
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

  useEffect(() => {
    const requested = search.agent;
    const nextAgent =
      requested &&
      (requested === SKILL_AUTHOR_ID ||
        agents.some((a) => a.agent_id === requested))
        ? requested
        : (agents[0]?.agent_id ?? null);
    if (nextAgent !== agentId) setAgentId(nextAgent);
  }, [agents, agentId, search.agent]);

  const agent = useMemo(
    () =>
      agentId === SKILL_AUTHOR_ID
        ? (authorQuery.data ?? null)
        : (agents.find((a) => a.agent_id === agentId) ?? null),
    [agents, agentId, authorQuery.data],
  );

  const threadsQuery = useQuery({
    queryKey: ["agents", "threads", agentId],
    queryFn: () => agentsApi.listThreads(agentId as string),
    enabled: Boolean(agentId),
  });
  const threads = threadsQuery.data?.threads ?? [];

  // On a refresh the URL can name an agent with no thread. Open the newest
  // conversation rather than an empty pane: the history is right there, and a
  // reader following a bookmark expects the conversation they left.
  useEffect(() => {
    if (
      view !== "chat" ||
      search.thread ||
      !threadsQuery.isSuccess ||
      freshChat
    )
      return;
    const newest = threads[0];
    if (!newest) return;
    navigate({
      to: "/studio",
      search: { agent: agentId ?? undefined, thread: newest.thread_id },
      replace: true,
    });
  }, [
    agentId,
    freshChat,
    navigate,
    search.thread,
    threads,
    threadsQuery.isSuccess,
    view,
  ]);

  const selectAgent = (id: string) => {
    setMobileSidebarOpen(false);
    setInitialPrompt("");
    setAgentId(id);
    setNewChatNonce(0);
    setView("chat");
    // A new agent starts a fresh conversation, so the thread is dropped.
    setFreshChat(true);
    navigate({ to: "/studio", search: { agent: id }, replace: true });
  };

  const selectThread = (id: string) => {
    setMobileSidebarOpen(false);
    setInitialPrompt("");
    setView("chat");
    navigate({
      to: "/studio",
      search: { agent: agentId ?? undefined, thread: id },
      replace: true,
    });
  };

  const startNewChat = () => {
    setMobileSidebarOpen(false);
    setInitialPrompt("");
    setView("chat");
    setFreshChat(true);
    setNewChatNonce((n) => n + 1);
    navigate({
      to: "/studio",
      search: { agent: agentId ?? undefined },
      replace: true,
    });
  };

  const removeThread = useMutation({
    mutationFn: (threadId: string) =>
      agentsApi.deleteThread(agentId as string, threadId),
    onSuccess: (_result, threadId) => {
      queryClient.invalidateQueries({
        queryKey: ["agents", "threads", agentId],
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
    onSuccess: (selected) => {
      selectAgent(selected.agent_id);
      setNewChatNonce((n) => n + 1);
      setInitialPrompt(`${CREATE_SKILL_COMMAND} `);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const renameThread = useMutation({
    mutationFn: ({ threadId, title }: { threadId: string; title: string }) =>
      agentsApi.renameThread(agentId as string, threadId, title),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agents", "threads", agentId],
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
        className={
          isMobile && mobileSidebarOpen
            ? "absolute inset-y-0 left-0 z-40 bg-background"
            : "shrink-0"
        }
      >
        <StudioSidebar
          view={view}
          onView={(nextView) => {
            setView(nextView);
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
          threadsLoading={threadsQuery.isLoading}
          open={visibleSidebarOpen}
          onToggle={toggleSidebar}
          footer={
            <StudioAccountMenu
              identity={settingsQuery.data?.identity}
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

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
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
          />
        ) : view === "artifacts" ? (
          <StudioArtifacts onSelectAgent={selectAgent} />
        ) : view === "dashboards" ? (
          <StudioDashboards />
        ) : (
          <StudioCapabilities
            onCreateWithChat={() => {
              if (!createSkillChat.isPending) createSkillChat.mutate();
            }}
            creatingChat={createSkillChat.isPending}
          />
        )}
      </main>
    </div>
  );
}
