import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { toast } from "sonner";
import { getCookie, setCookie } from "@/lib/cookies";
import { agentsApi, studioApi } from "@/features/agents/api";
import { StudioChat } from "./studio-chat";
import { StudioSidebar, type StudioView } from "./studio-sidebar";
import { StudioAccountMenu } from "./studio-account-menu";
import { StudioArtifacts } from "./studio-artifacts";
import { StudioCapabilities } from "./studio-capabilities";
import { StudioSettingsDialog } from "./studio-settings";
import type { StudioSettings } from "@/features/agents/api";

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
    queryKey: ["agents", "list"],
    queryFn: () => agentsApi.list(),
  });
  const agents = agentsQuery.data?.agents ?? [];

  const settingsQuery = useQuery({
    queryKey: ["studio", "settings"],
    queryFn: () => studioApi.settings(),
  });

  const [agentId, setAgentId] = useState<string | null>(null);
  const [view, setView] = useState<StudioView>(search.view ?? "chat");
  const [settingsOpen, setSettingsOpen] = useState(false);
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
  // Shared with Nova's main sidebar, so the rail remembers its state whichever
  // surface set it last.
  const [sidebarOpen, setSidebarOpen] = useState(
    () => getCookie("sidebar_state") !== "false",
  );

  const toggleSidebar = () => {
    setSidebarOpen((prev) => {
      setCookie("sidebar_state", prev ? "false" : "true");
      return !prev;
    });
  };

  useEffect(() => {
    const requested = search.agent;
    const nextAgent =
      requested && agents.some((a) => a.agent_id === requested)
        ? requested
        : (agents[0]?.agent_id ?? null);
    if (nextAgent !== agentId) setAgentId(nextAgent);
  }, [agents, agentId, search.agent]);

  const agent = useMemo(
    () => agents.find((a) => a.agent_id === agentId) ?? null,
    [agents, agentId],
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
    if (search.thread || !threadsQuery.isSuccess || freshChat) return;
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
  ]);

  const updateSettings = useMutation({
    mutationFn: (patch: Partial<StudioSettings["preferences"]>) =>
      studioApi.updateSettings(patch),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["studio", "settings"] });
      toast.success("Settings saved");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const selectAgent = (id: string) => {
    setAgentId(id);
    setNewChatNonce(0);
    setView("chat");
    // A new agent starts a fresh conversation, so the thread is dropped.
    setFreshChat(true);
    navigate({ to: "/studio", search: { agent: id }, replace: true });
  };

  const selectThread = (id: string) => {
    setView("chat");
    setFreshChat(false);
    navigate({
      to: "/studio",
      search: { agent: agentId ?? undefined, thread: id },
      replace: true,
    });
  };

  const startNewChat = () => {
    setView("chat");
    setFreshChat(true);
    setNewChatNonce((n) => n + 1);
    navigate({
      to: "/studio",
      search: { agent: agentId ?? undefined },
      replace: true,
    });
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <StudioSidebar
        view={view}
        onView={setView}
        onOpenSettings={() => setSettingsOpen(true)}
        threads={threads}
        activeThreadId={activeThreadId}
        onOpenThread={selectThread}
        onNewChat={startNewChat}
        threadsLoading={threadsQuery.isLoading}
        open={sidebarOpen}
        onToggle={toggleSidebar}
        footer={
          <StudioAccountMenu
            identity={settingsQuery.data?.identity}
            onIdentityChange={() =>
              queryClient.invalidateQueries({
                queryKey: ["studio", "settings"],
              })
            }
            collapsed={!sidebarOpen}
          />
        }
      />

      <main className="flex min-w-0 flex-1 flex-col">
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
              // A real id means the first message created a thread. A null id
              // comes from the header's New chat action and must keep the
              // auto-open guard enabled, or the latest thread immediately
              // reappears and makes the button look broken.
              setFreshChat(id === null);
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
          />
        ) : view === "artifacts" ? (
          <StudioArtifacts onSelectAgent={selectAgent} />
        ) : (
          <StudioCapabilities onSelectAgent={selectAgent} />
        )}
      </main>

      <StudioSettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        settings={settingsQuery.data}
        onSave={(patch) => updateSettings.mutate(patch)}
        saving={updateSettings.isPending}
      />
    </div>
  );
}
