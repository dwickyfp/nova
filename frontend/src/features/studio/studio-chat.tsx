import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  FileText,
  ImageIcon,
  RefreshCcw,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import type { AssistantEvent } from "@/features/assistant/types";
import {
  AUTO_AGENT_ID,
  agentsApi,
  streamAgentTurn,
  studioApi,
  type Agent,
  type AgentMessage,
} from "@/features/agents/api";
import { ProcessRail, StopNotice, type RailStep } from "./thought-turn";
import { ConsentCard } from "./consent-card";
import { TurnContent } from "./turn-content";
import { findArtifactSql, type SavableContent } from "./artifact-source";
import { splitChartTitle } from "./result-cards";
import {
  CREATE_SKILL_COMMAND,
  SKILL_AUTHOR_ID,
  extractSkillDraft,
} from "./skill-document";
import { SkillEditor } from "./skill-editor";
import { AnswerFooter, type AnswerFeedback } from "./answer-footer";
import { DeepResearchCard } from "./deep-research-card";
import { deepResearchApi } from "@/features/agents/studio-intelligence-api";
import { UserMessageFooter } from "./user-message-footer";
import { AutoSubagentCard, AutoSubagentPanel } from "./auto-subagent-card";
import { AutoTurnRail } from "./auto-turn-rail";
import { rootRunForTurn } from "./auto-run-timeline";
import {
  MAX_FILES,
  MAX_TOTAL_BYTES,
  MAX_TEXT_TOTAL_BYTES,
  readAttachment,
  type PendingAttachment,
} from "./studio-attachments";
import { Composer } from "./studio-composer";
import { useTranscriptStream } from "./use-transcript-stream";
import { WorkflowRail } from "./workflow-rail";
import { ThreadMissionObjects } from "./mission-objects";
import { mergeMissionProjection, type Mission } from "./workflow-api";
import { useAuthStore } from "@/stores/auth-store";
import { patchTurn, replayThread, type PendingCall, type TranscriptTurn } from "./studio-transcript";
export { applyEvent, replayThread } from "./studio-transcript";
export type { OrderedContent, TranscriptTurn } from "./studio-transcript";


let turnSeq = 0;
function nextTurnId(): string {
  turnSeq += 1;
  return `turn-${turnSeq}`;
}

/**
 * Nova Studio's transcript.
 *
 * Structurally this follows Snowflake CoWork: the question on the right, then
 * one grouped process rail, then the answer with its result blocks. It is not a
 * chat log of every event. The agentic work is transparent when you open it and
 * invisible when you do not, which is the only way a long transcript stays
 * readable.
 */
export function StudioChat({
  agent,
  agents,
  onSelectAgent,
  currentRole,
  displayName,
  activeThreadId,
  onThreadChange,
  newChatNonce = 0,
  initialPrompt = "",
}: {
  agent: Agent | null;
  agents: Agent[];
  onSelectAgent: (id: string) => void;
  currentRole: string | null;
  /** Preferred Studio greeting name, falling back to the signed-in username. */
  displayName?: string | null;
  /** The thread the sidebar has open, or null for a fresh conversation. */
  activeThreadId: string | null;
  /** Report a newly created thread up, so the sidebar can highlight it. */
  onThreadChange: (threadId: string | null) => void;
  /** Bumped by the sidebar's "New" control to start a fresh conversation. */
  newChatNonce?: number;
  initialPrompt?: string;
}) {
  const queryClient = useQueryClient();
  // `threadId` records the thread whose transcript is actually loaded. It
  // starts empty even when the URL names a thread, so the load effect below
  // always runs for that thread instead of assuming it is already in hand.
  const [threadId, setThreadId] = useState<string | null>(null);
  const [research, setResearch] = useState<{ agentId: string; runId: string } | null>(null);
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [workflowAvailable, setWorkflowAvailable] = useState(false);
  const [workflowOpen, setWorkflowOpen] = useState(false);
  const workflowButtonRef = useRef<HTMLButtonElement | null>(null);
  const changeWorkflowOpen = useCallback((open: boolean) => {
    setWorkflowOpen(open);
    if (!open) requestAnimationFrame(() => workflowButtonRef.current?.focus());
  }, []);
  const storedMessagesRef = useRef<AgentMessage[]>([]);
  const [olderCursor, setOlderCursor] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const historyRequestRef = useRef(0);
  const prependScrollRef = useRef<{ height: number; top: number } | null>(null);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [readingFiles, setReadingFiles] = useState(false);
  const [skillDraft, setSkillDraft] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [activeAutoRun, setActiveAutoRun] = useState<{
    runId: string;
    threadId: string;
  } | null>(null);
  const [selectedChild, setSelectedChild] = useState<{ rootRunId: string; childRunId: string } | null>(null);
  const childOpenerRef = useRef<HTMLElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const openChild = useCallback((rootRunId: string, childRunId: string) => {
    childOpenerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setSelectedChild({ rootRunId, childRunId });
  }, []);
  const closeChild = useCallback(() => {
    setSelectedChild(null);
    requestAnimationFrame(() => {
      if (childOpenerRef.current?.isConnected) childOpenerRef.current.focus();
      else textareaRef.current?.focus();
    });
  }, []);
  useEffect(() => setSelectedChild(null), [activeThreadId]);
  useEffect(() => setWorkflowOpen(false), [activeThreadId, agent?.agent_id]);
  const activeAutoRunId = activeAutoRun?.threadId === activeThreadId
    ? activeAutoRun.runId
    : null;
  const refreshedAutoRunsRef = useRef<Set<string>>(new Set());
  const autoRunsQuery = useQuery({
    queryKey: ["studio", "auto-runs", activeThreadId],
    queryFn: () => agentsApi.listAutoThreadRuns(activeThreadId as string),
    enabled: agent?.agent_id === AUTO_AGENT_ID && Boolean(activeThreadId),
  });
  const displayedAutoRunId = activeThreadId && activeThreadId === threadId
    ? activeAutoRunId ?? autoRunsQuery.data?.runs[0]?.run_id ?? null
    : null;
  const autoTreeQuery = useQuery({
    queryKey: ["studio", "auto-tree", displayedAutoRunId],
    queryFn: () => agentsApi.getAutoRunTree(displayedAutoRunId as string),
    enabled: agent?.agent_id === AUTO_AGENT_ID && Boolean(displayedAutoRunId),
    refetchInterval: (query) => {
      const status = query.state.data?.runs.find((run) => run.depth === 0)?.status;
      return streaming || (status && !["completed", "failed", "cancelled", "interrupted"].includes(status)) ? 1000 : false;
    },
  });
  const historicalChildTreeQuery = useQuery({
    queryKey: ["studio", "auto-tree", selectedChild?.rootRunId],
    queryFn: () => agentsApi.getAutoRunTree(selectedChild?.rootRunId as string),
    enabled: agent?.agent_id === AUTO_AGENT_ID && Boolean(selectedChild?.rootRunId && selectedChild.rootRunId !== displayedAutoRunId),
  });
  useEffect(() => {
    if (!streaming && (!activeThreadId || (threadId && activeThreadId !== threadId))) {
      setActiveAutoRun(null);
    }
  }, [activeThreadId, threadId, streaming]);
  useEffect(() => {
    const root = autoTreeQuery.data?.runs.find((run) => run.depth === 0);
    if (!root || !["completed", "failed", "cancelled", "interrupted"].includes(root.status)
        || streaming || !activeThreadId || activeThreadId !== threadId
        || refreshedAutoRunsRef.current.has(root.run_id)) return;
    refreshedAutoRunsRef.current.add(root.run_id);
    void agentsApi.getThread(AUTO_AGENT_ID, activeThreadId).then((detail) => {
      if (activeThreadIdRef.current === activeThreadId) {
        const newestIds = new Set(detail.messages.map((message) => message.message_id));
        const hasEarlierMessages = storedMessagesRef.current.some((message) => !newestIds.has(message.message_id));
        storedMessagesRef.current = [...new Map([...storedMessagesRef.current, ...detail.messages].map((m) => [m.message_id, m])).values()];
        setTurns(replayThread(storedMessagesRef.current));
        if (!hasEarlierMessages) setOlderCursor(detail.next_cursor ?? null);
      }
    }).catch(() => {
      refreshedAutoRunsRef.current.delete(root.run_id);
    });
  }, [autoTreeQuery.data, activeThreadId, threadId, streaming]);
  const [loadingThread, setLoadingThread] = useState(false);
  const [extended, setExtended] = useState(false);
  const [deepenTarget, setDeepenTarget] = useState<string | null>(null);
  const [savingContentId, setSavingContentId] = useState<string | null>(null);
  const [savedContentIds, setSavedContentIds] = useState<Set<string>>(
    () => new Set(),
  );
  const { abortRef, queueEvent, flushEvents, discardQueuedEvents } = useTranscriptStream(setTurns);
  const onTurnEvent = useCallback((turnId: string, event: AssistantEvent) => {
    const workflow = event as unknown as { type: string; mission?: Mission };
    if (workflow.type === "mission_updated" && workflow.mission) {
      const mission = workflow.mission;
      queryClient.setQueryData<{ missions: Mission[] }>(
        ["studio", "workflow", useAuthStore.getState().securityEpoch, mission.thread_id],
        (current) => mergeMissionProjection(current, mission, mission.thread_id),
      );
    }
    queueEvent(turnId, event);
  }, [queryClient, queueEvent]);
  const composerRef = useRef<HTMLDivElement | null>(null);
  const composerStartRef = useRef<DOMRect | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const followOutputRef = useRef(true);
  useLayoutEffect(() => {
    const previous = prependScrollRef.current;
    const el = scrollRef.current;
    if (previous && el) {
      el.scrollTop = previous.top + el.scrollHeight - previous.height;
      prependScrollRef.current = null;
    }
  }, [turns]);

  const loadOlderMessages = async () => {
    if (!agent || !threadId || !olderCursor || loadingOlder || streaming) return;
    const request = historyRequestRef.current;
    const target = threadId;
    setLoadingOlder(true);
    try {
      const page = await agentsApi.getThread(agent.agent_id, target, olderCursor);
      if (request !== historyRequestRef.current || activeThreadIdRef.current !== target) return;
      const previousIds = new Set(replayThread(storedMessagesRef.current).map((turn) => turn.id));
      storedMessagesRef.current = [...new Map([...page.messages, ...storedMessagesRef.current].map((m) => [m.message_id, m])).values()];
      const restored = replayThread(storedMessagesRef.current);
      const el = scrollRef.current;
      if (el) prependScrollRef.current = { height: el.scrollHeight, top: el.scrollTop };
      followOutputRef.current = false;
      setTurns((current) => {
        const byId = new Map(current.map((turn) => [turn.id, turn]));
        return [...restored.map((turn) => byId.get(turn.id) ?? turn), ...current.filter((turn) => !previousIds.has(turn.id))];
      });
      setOlderCursor(page.next_cursor ?? null);
    } catch {
      if (request === historyRequestRef.current) toast.error("Could not load older messages. Try again.");
    } finally {
      if (request === historyRequestRef.current) setLoadingOlder(false);
    }
  };
  const scrollFrameRef = useRef<number | null>(null);
  // New chat clears local state before the router necessarily removes the old
  // thread from the URL. Remember that one stale id so it cannot be reloaded.
  const staleThreadAfterNewChatRef = useRef<string | null>(null);
  const activeThreadIdRef = useRef(activeThreadId);
  activeThreadIdRef.current = activeThreadId;

  // Clear the previous agent's state before opening the selected conversation.
  useEffect(() => {
    abortRef.current?.abort();
    discardQueuedEvents();
    followOutputRef.current = true;
    setThreadId(null);
    setTurns([]);
    historyRequestRef.current += 1;
    storedMessagesRef.current = [];
    setOlderCursor(null);
    setLoadingOlder(false);
    setActiveAutoRun(null);
    setSelectedChild(null);
    setInput("");
    setAttachments([]);
    setDeepenTarget(null);
    setSavingContentId(null);
    setSavedContentIds(new Set());
  }, [agent?.agent_id, discardQueuedEvents]);

  /**
   * Load the thread the sidebar asked for.
   *
   * The transcript is rebuilt from the stored trace, so the reasoning and the
   * result blocks come back with the answers. Continuing the conversation then
   * appends to the same thread, because the loop replays its full history.
   */
  useEffect(() => {
    if (!activeThreadId) {
      staleThreadAfterNewChatRef.current = null;
      return;
    }
    if (activeThreadId === staleThreadAfterNewChatRef.current) return;
    staleThreadAfterNewChatRef.current = null;
    if (!agent || activeThreadId === threadId) return;
    let cancelled = false;
    historyRequestRef.current += 1;
    setLoadingOlder(false);
    setLoadingThread(true);
    abortRef.current?.abort();
    discardQueuedEvents();
    followOutputRef.current = true;
    agentsApi
      .getThread(agent.agent_id, activeThreadId)
      .then((detail) => {
        if (cancelled) return;
        setThreadId(activeThreadId);
        storedMessagesRef.current = detail.messages;
        setOlderCursor(detail.next_cursor ?? null);
        setTurns(replayThread(detail.messages));
        setAttachments([]);
        setDeepenTarget(null);
        setSavingContentId(null);
        setSavedContentIds(new Set());
      })
      .catch(() => {
        // A thread that cannot be read leaves the current view untouched: a
        // failed open must not cost the conversation already in hand.
      })
      .finally(() => {
        // Cleared unconditionally. Guarding this on `cancelled` left the flag
        // stuck when an effect re-ran (StrictMode mounts twice in dev): the
        // first run's cleanup cancelled its own clear, and the second run
        // returned early without reaching this line, so the pane showed
        // "Opening the conversation" forever.
        setLoadingThread(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeThreadId, agent, discardQueuedEvents, threadId]);

  // The sidebar's "New" control: drop to a fresh conversation.
  useEffect(() => {
    if (newChatNonce === 0) return;
    staleThreadAfterNewChatRef.current = activeThreadIdRef.current;
    abortRef.current?.abort();
    discardQueuedEvents();
    followOutputRef.current = true;
    setThreadId(null);
    setActiveAutoRun(null);
    setTurns([]);
    historyRequestRef.current += 1;
    storedMessagesRef.current = [];
    setOlderCursor(null);
    setLoadingOlder(false);
    setInput("");
    setAttachments([]);
    setDeepenTarget(null);
    setSavingContentId(null);
    setSavedContentIds(new Set());
  }, [discardQueuedEvents, newChatNonce]);

  useEffect(() => {
    if (initialPrompt) {
      setInput(initialPrompt);
      textareaRef.current?.focus();
    }
  }, [initialPrompt, newChatNonce, agent?.agent_id]);

  // Follow the newest content, but only while the user is already at the
  // bottom, so scrolling back to read an earlier step is not yanked away.
  const onTranscriptScroll = useCallback(
    (event: React.UIEvent<HTMLDivElement>) => {
      const el = event.currentTarget;
      followOutputRef.current =
        el.scrollHeight - el.scrollTop - el.clientHeight < 160;
    },
    [],
  );
  useEffect(() => {
    if (!followOutputRef.current) return;
    if (scrollFrameRef.current !== null) {
      window.cancelAnimationFrame(scrollFrameRef.current);
    }
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      const el = scrollRef.current;
      if (el && followOutputRef.current) el.scrollTop = el.scrollHeight;
    });
    return () => {
      if (scrollFrameRef.current !== null) {
        window.cancelAnimationFrame(scrollFrameRef.current);
        scrollFrameRef.current = null;
      }
    };
  }, [turns]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  useLayoutEffect(() => {
    if (turns.length === 0 || !composerStartRef.current || !composerRef.current)
      return;
    const before = composerStartRef.current;
    composerStartRef.current = null;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const after = composerRef.current.getBoundingClientRect();
    const offset = before.top - after.top;
    if (Math.abs(offset) < 2) return;
    composerRef.current.animate(
      [
        { transform: `translateY(${offset}px)` },
        { transform: "translateY(0)" },
      ],
      { duration: 420, easing: "cubic-bezier(0.22, 1, 0.36, 1)" },
    );
  }, [turns.length]);

  const ensureThread = useCallback(
    async (title: string): Promise<string | null> => {
      if (!agent) return null;
      if (threadId) return threadId;
      // A thread the URL selected may still be loading. Sending into it must
      // continue that conversation, not silently start a second one.
      if (
        activeThreadId &&
        activeThreadId !== staleThreadAfterNewChatRef.current
      ) {
        return activeThreadId;
      }
      // The first question names the conversation, so the history list is
      // readable from the moment it appears. The backend keeps it in step on
      // the first turn; this only avoids a placeholder flashing first.
      const thread = await agentsApi.createThread(agent.agent_id, title);
      setThreadId(thread.thread_id);
      // Tell the sidebar, so the new conversation is the highlighted one and the
      // history list refreshes to include it.
      onThreadChange(thread.thread_id);
      void queryClient.invalidateQueries({ queryKey: ["agents", "threads"] });
      return thread.thread_id;
    },
    [activeThreadId, agent, onThreadChange, queryClient, threadId],
  );

  const addFiles = useCallback(async (files: FileList) => {
    setReadingFiles(true);
    try {
      const added = await Promise.all(Array.from(files, readAttachment));
      setAttachments((current) => {
        const next = [...current, ...added];
        if (next.length > MAX_FILES) {
          toast.error(`Attach at most ${MAX_FILES} files.`);
          return current;
        }
        if (next.reduce((total, item) => total + item.sizeBytes, 0) > MAX_TOTAL_BYTES) {
          toast.error("Attachments must be 4 MB or smaller in total.");
          return current;
        }
        if (next.filter((item) => item.mediaType === "text/plain")
          .reduce((total, item) => total + item.sizeBytes, 0) > MAX_TEXT_TOTAL_BYTES) {
          toast.error("Text attachments must be 64 KB or smaller in total.");
          return current;
        }
        if (new Set(next.map((item) => item.name)).size !== next.length) {
          toast.error("A file with that name is already attached.");
          return current;
        }
        return next;
      });
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setReadingFiles(false);
    }
  }, []);

  const send = useCallback(
    async (override?: string) => {
      const content = (override ?? input).trim();
      const pending = attachments;
      if ((!content && !pending.length) || streaming || readingFiles || abortRef.current || !agent) return;

      if (turns.length === 0) {
        composerStartRef.current =
          composerRef.current?.getBoundingClientRect() ?? null;
      }
      const controller = new AbortController();
      abortRef.current = controller;
      const turnId = nextTurnId();
      setTurns((prev) => [
        ...prev,
        {
          id: turnId,
          question: content,
          questionCreatedAt: new Date().toISOString(),
          attachments: pending.map(({ name, sizeBytes, mediaType }) => ({ name, sizeBytes, mediaType })),
          model: agent.model_name ?? undefined,
          steps: [],
          answer: "",
          content: [],
          pendingConsent: null,
          blocks: { tables: [], charts: [], citations: [] },
          state: "streaming",
        },
      ]);
      setInput("");
      setAttachments([]);
      setStreaming(true);
      setDeepenTarget(null);

      let accepted = false;
      let submittedThread: string | null = null;
      try {
        const thread = await ensureThread(content || pending[0].name);
        if (!thread) throw new Error("Could not start the conversation");
        submittedThread = thread;
        if (controller.signal.aborted) return;
        await streamAgentTurn(agent.agent_id, thread, content, {
          signal: controller.signal,
          role: currentRole,
          attachments: pending.map(({ name, content: fileContent, mediaType }) => ({
            name, content: fileContent, media_type: mediaType,
          })),
          onAccepted: () => { accepted = true; },
          onRunId: (runId) => {
            if (agent.agent_id === AUTO_AGENT_ID) {
              setActiveAutoRun({ runId, threadId: thread });
              void queryClient.invalidateQueries({ queryKey: ["studio", "auto-runs", thread] });
            }
          },
          onEvent: (event: AssistantEvent) => onTurnEvent(turnId, event),
        });
      } catch (error) {
        if ((error as Error).name !== "AbortError" && !controller.signal.aborted) {
          if (!accepted) {
            setInput((current) => current || content);
            setAttachments((current) => current.length ? current : pending);
          }
          setTurns((prev) =>
            patchTurn(prev, turnId, (turn) => ({
              ...turn,
              state: "error",
              error: (error as Error).message,
            })),
          );
        }
      } finally {
        flushEvents();
        setTurns((prev) =>
          prev.map((turn) =>
            turn.id === turnId && turn.state === "streaming"
              ? { ...turn, state: "done" }
              : turn,
          ),
        );
        setStreaming(false);
        abortRef.current = null;
        queryClient.invalidateQueries({
          queryKey: ["agents", "threads"],
        });
        void queryClient.invalidateQueries({ queryKey: ["studio", "workflow"] });
        if (agent.agent_id === AUTO_AGENT_ID) {
          void queryClient.invalidateQueries({ queryKey: ["studio", "auto-runs"] });
        } else if (submittedThread && queryClient.getQueryData<{ missions: Mission[] }>(["studio", "workflow", useAuthStore.getState().securityEpoch, submittedThread])?.missions) {
          const target = submittedThread;
          const epoch = useAuthStore.getState().securityEpoch;
          void agentsApi.getThread(agent.agent_id, target).then((detail) => {
            if (activeThreadIdRef.current !== target || useAuthStore.getState().securityEpoch !== epoch) return;
            const persisted = replayThread(detail.messages);
            setTurns((current) => current.map((turn) => {
              const saved = persisted.find((item) => item.messageId && item.messageId === turn.messageId);
              return saved?.evidence ? { ...turn, evidence: saved.evidence } : turn;
            }));
          }).catch(() => {
            // A failed evidence refresh leaves the persisted replay available on reopen.
          });
        }
      }
    },
    [
      agent,
      attachments,
      currentRole,
      ensureThread,
      flushEvents,
      input,
      queryClient,
      queueEvent,
      onTurnEvent,
      readingFiles,
      streaming,
      turns.length,
    ],
  );

  const stop = useCallback(() => {
    if (agent?.agent_id === AUTO_AGENT_ID && activeAutoRun) {
      void agentsApi.cancelAutoRun(activeAutoRun.runId).catch(() => {
        toast.error("Could not cancel the Smart run. Check its activity and try again.");
      });
    }
    abortRef.current?.abort();
    flushEvents();
    setStreaming(false);
    setTurns((prev) =>
      prev.map((turn) =>
        turn.state === "streaming" ? { ...turn, state: "cancelled" } : turn,
      ),
    );
  }, [activeAutoRun, agent?.agent_id, flushEvents]);

  /**
   * Send the user's decision on a tool call, then clear the card. The closing
   * `tool_status` frame arrives on the still-open turn stream, so the rail
   * updates itself; this only needs to unblock the turn.
   */
  const decide = useCallback(
    async (
      turnId: string,
      call: PendingCall,
      decision: "allow_once" | "allow_session" | "deny",
    ) => {
      if (!agent) return;
      await agentsApi.decideToolCall(
        agent.agent_id,
        call.tool_call_id,
        decision,
      );
      setTurns((prev) =>
        patchTurn(prev, turnId, (turn) => ({
          ...turn,
          pendingConsent: null,
          revealKey: (turn.revealKey ?? 0) + 1,
          steps: [
            ...turn.steps,
            {
              id: `${call.tool_call_id}-decision`,
              kind: "consent",
              label: decision === "deny" ? "denied" : "approved",
              text:
                decision === "deny"
                  ? `Denied ${call.tool_name}`
                  : `Approved ${call.tool_name}`,
              status: decision === "deny" ? "denied" : "done",
            },
          ],
        })),
      );
    },
    [agent],
  );

  /**
   * Extended thinking, implemented honestly.
   *
   * The provider exposes no hidden reasoning channel, and Nova will not
   * pretend one exists. This asks the agent a follow-up that pushes on the
   * answer it already gave, and streams the response into the same pane as a
   * deepening pass. It is Nova's own re-run, and it is labelled as one.
   */
  const deepen = useCallback(
    async (turn: TranscriptTurn) => {
      if (!agent || !threadId || !turn.answer || streaming) return;
      setDeepenTarget(turn.id);
      const prompt =
        "Reconsider your previous answer. Work through the reasoning step by step, " +
        "state any assumption you made, name any weakness in the numbers, and give a " +
        "revised conclusion. Do not repeat the original answer verbatim.";
      setStreaming(true);
      const controller = new AbortController();
      abortRef.current = controller;
      const deepenTurnId = nextTurnId();
      setTurns((prev) => [
        ...prev,
        {
          id: deepenTurnId,
          question: "Reconsider the previous answer, step by step.",
          origin: "reconsider",
          model: agent.model_name ?? undefined,
          steps: [],
          answer: "",
          content: [],
          pendingConsent: null,
          blocks: { tables: [], charts: [], citations: [] },
          state: "streaming",
        },
      ]);
      try {
        await streamAgentTurn(agent.agent_id, threadId, prompt, {
          signal: controller.signal,
          role: currentRole,
          onEvent: (event) => onTurnEvent(deepenTurnId, event),
        });
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          setTurns((prev) =>
            patchTurn(prev, deepenTurnId, (t) => ({
              ...t,
              state: "error",
              error: (error as Error).message,
            })),
          );
        }
      } finally {
        flushEvents();
        setTurns((prev) =>
          prev.map((t) =>
            t.id === deepenTurnId && t.state === "streaming"
              ? { ...t, state: "done" }
              : t,
          ),
        );
        setStreaming(false);
        setDeepenTarget(null);
        abortRef.current = null;
      }
    },
    [agent, currentRole, flushEvents, queueEvent, streaming, threadId],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  const saveArtifact = useCallback(
    async (turn: TranscriptTurn, item: SavableContent) => {
      if (!agent || savingContentId) return;
      const sql = findArtifactSql(turns, turn.id, item);
      if (!sql) {
        toast.error("This result has no reusable SQL to save.");
        return;
      }

      let chartSpec: Record<string, unknown> | null = null;
      let title =
        item.type === "table"
          ? item.block.title?.trim() || turn.question.trim()
          : turn.question.trim();
      if (item.type === "chart") {
        const chartTitle = splitChartTitle(item.block.chart_spec).title;
        title = chartTitle || turn.question.trim() || "Saved chart";
        try {
          chartSpec = JSON.parse(item.block.chart_spec) as Record<
            string,
            unknown
          >;
        } catch {
          toast.error("This chart specification cannot be saved.");
          return;
        }
      }
      title = title.slice(0, 256) || "Saved result";

      setSavingContentId(item.id);
      try {
        await studioApi.createArtifact({
          title,
          artifact_type: item.type,
          sql_text: sql,
          database_name: agent.database_name,
          schema_name: agent.schema_name,
          chart_spec: chartSpec,
          agent_id: agent.agent_id,
          thread_id: threadId ?? activeThreadId,
        });
        setSavedContentIds((current) => new Set(current).add(item.id));
        await queryClient.invalidateQueries({
          queryKey: ["studio", "artifacts"],
        });
        toast.success("Artifact saved");
      } catch (error) {
        toast.error((error as Error).message || "Artifact could not be saved.");
      } finally {
        setSavingContentId(null);
      }
    },
    [activeThreadId, agent, queryClient, savingContentId, threadId, turns],
  );

  // Whether an answer offers a reconsider pass is a preference, not a
  // per-message control; it is set in Settings and read here.
  useEffect(() => {
    studioApi.settings().then(
      (settings) =>
        setExtended(Boolean(settings?.preferences?.extended_thinking)),
      () => undefined,
    );
  }, []);

  const saveFeedback = useCallback(async (turn: TranscriptTurn, feedback: AnswerFeedback) => {
    if (!agent || !threadId || !turn.messageId) throw new Error("Answer is not saved");
    const result = await agentsApi.setMessageFeedback(agent.agent_id, threadId, turn.messageId, feedback);
    setTurns((prev) => patchTurn(prev, turn.id, (current) => ({ ...current, feedback: result.feedback })));
  }, [agent, threadId]);

  const researchable = Boolean(
    agent && agent.agent_id !== AUTO_AGENT_ID && agent.agent_id !== SKILL_AUTHOR_ID && !streaming,
  );
  const startResearch = async () => {
    const question = input.trim();
    if (!agent || question.length < 3) return;
    try {
      const started = await deepResearchApi.start(agent.agent_id, question);
      setResearch({ agentId: agent.agent_id, runId: started.run_id });
      setInput("");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Deep research could not start.");
    }
  };

  const empty = turns.length === 0;
  const welcome = empty && (!agent || !loadingThread);
  const showAutoCard = agent?.agent_id === AUTO_AGENT_ID && Boolean(displayedAutoRunId);
  const selectedTreeQuery = selectedChild?.rootRunId === displayedAutoRunId ? autoTreeQuery : historicalChildTreeQuery;
  const chronologicalAutoRuns = [...(autoRunsQuery.data?.runs ?? [])].reverse();
  const autoTurns = turns.filter((turn) => turn.origin !== "reconsider");
  const creatingSkill =
    agent?.agent_id === SKILL_AUTHOR_ID ||
    input.trimStart().startsWith(CREATE_SKILL_COMMAND) ||
    turns.some(
      (turn) => turn.question.trim().split(/\s+/)[0] === CREATE_SKILL_COMMAND,
    );

  return (
    <div
      className={cn(
        "relative flex min-h-0 min-w-0 flex-1 overflow-hidden",
      )}
    >
      <div className={cn("relative flex min-h-0 min-w-0 flex-1 flex-col", welcome && "overflow-y-auto", selectedChild && "hidden lg:flex")}>
      {workflowAvailable && !selectedChild && <div className="flex shrink-0 justify-end border-b border-border px-4 py-2 xl:hidden"><Button ref={workflowButtonRef} variant="ghost" className="min-h-11" aria-label="Open workflow details" aria-haspopup="dialog" onClick={() => changeWorkflowOpen(true)}>Activity and evidence</Button></div>}
      {showAutoCard && !selectedChild && !workflowAvailable ? (
        <AutoSubagentCard
          key={displayedAutoRunId}
          runs={autoTreeQuery.data?.runs ?? []}
          agents={agents}
          loading={autoTreeQuery.isLoading}
          error={autoTreeQuery.isError}
          retry={() => void autoTreeQuery.refetch()}
          onSelectChild={(childRunId) => openChild(displayedAutoRunId as string, childRunId)}
        />
      ) : null}
      <div
        className={cn(
          "flex flex-1 flex-col",
          welcome ? "justify-center pb-4 pt-12" : "min-h-0",
        )}
      >
        <div
          ref={scrollRef}
          className={cn(
            "w-full",
            welcome ? "shrink-0" : "min-h-0 flex-1 overflow-y-auto",
            showAutoCard && !selectedChild && !workflowAvailable && "xl:pr-[324px]",
          )}
          onScroll={onTranscriptScroll}
        >
          <div
            className={cn(
              "mx-auto w-full max-w-3xl px-4 sm:px-6",
              welcome ? "pb-5" : "py-8 sm:py-10",
            )}
          >
            {agent && loadingThread && empty ? (
              <p className="pt-20 text-center text-sm text-muted-foreground">
                Opening the conversation
              </p>
            ) : agent && empty && creatingSkill ? (
              <div className="py-12">
                <h1 className="text-2xl leading-8 font-normal">
                  Create a skill with Nova
                </h1>
                <p className="mt-3 max-w-lg text-sm leading-relaxed text-muted-foreground">
                  Describe a task you repeat, what Nova should ask for, and how
                  the result should look. You can refine the draft here before
                  saving it to your skills.
                </p>
              </div>
            ) : empty || !agent ? (
              <Greeting displayName={displayName} />
            ) : (
              <div className="flex flex-col gap-8">
                {olderCursor ? (
                  <Button type="button" variant="ghost" size="sm" className="h-auto self-center py-3 sm:py-2"
                    disabled={loadingOlder || streaming || loadingThread} onClick={() => void loadOlderMessages()}>
                    {loadingOlder ? "Loading…" : "Load older messages"}
                  </Button>
                ) : null}
                {turns.map((turn, index) => (
                  <TurnView
                    key={turn.id}
                    turn={turn}
                    onAsk={index === turns.length - 1 && !streaming ? (question) => void send(question) : undefined}
                    agent={agent}
                    agents={agents}
                    autoRunId={agent.agent_id === AUTO_AGENT_ID ? rootRunForTurn(turn, chronologicalAutoRuns, autoTurns, activeAutoRunId) : null}
                    onOpenChild={openChild}
                    deepenable={
                      extended && turn.state === "done" && Boolean(turn.answer)
                    }
                    deepening={deepenTarget === turn.id}
                    onDeepen={deepen}
                    reconsiderDisabled={streaming}
                    onFeedback={saveFeedback}
                    onDecide={decide}
                    onSaveArtifact={saveArtifact}
                    savingContentId={savingContentId}
                    savedContentIds={savedContentIds}
                    onReviewSkill={creatingSkill ? setSkillDraft : undefined}
                  />
                ))}
                {workflowAvailable && threadId && <ThreadMissionObjects threadId={threadId} onFollowUp={(prompt) => { setInput(prompt); textareaRef.current?.focus(); }} />}
              </div>
            )}
          </div>
        </div>

        <div
          ref={composerRef}
          data-testid="studio-composer"
          className={cn(
            "w-full shrink-0 px-4 sm:px-6",
            welcome ? "pb-2" : "pb-6 pt-2",
            showAutoCard && !selectedChild && !workflowAvailable && "xl:pr-[324px]",
          )}
        >
        {research ? (
          <div className="mx-auto w-full max-w-3xl">
            <DeepResearchCard
              agentId={research.agentId}
              runId={research.runId}
              onOpenReport={(threadId) => {
                setResearch(null);
                onThreadChange(threadId);
              }}
              onDismiss={() => setResearch(null)}
            />
          </div>
        ) : null}
        <Composer
            ref={textareaRef}
            value={input}
            onChange={setInput}
            onKeyDown={onKeyDown}
            onSend={() => void send()}
          onResearch={researchable ? () => void startResearch() : undefined}
          onStop={stop}
          readingFiles={readingFiles}
          attachments={attachments}
          onAddFiles={(files) => void addFiles(files)}
          onRemoveAttachment={(id) => setAttachments((current) => current.filter((item) => item.id !== id))}
            streaming={streaming}
            disabled={!agent}
            agent={agent}
            agents={agents}
            onSelectAgent={onSelectAgent}
          />
        </div>
      </div>
      {welcome &&
      !creatingSkill &&
      agent &&
      agent.sample_questions.length > 0 ? (
        <SuggestedQuestions
          questions={agent.sample_questions}
          onPick={(text) => void send(text)}
        />
      ) : null}
      {skillDraft ? (
        <SkillEditor
          initialDocument={skillDraft}
          onClose={() => setSkillDraft(null)}
        />
      ) : null}
      </div>
      {threadId && activeThreadId === threadId && !selectedChild && !creatingSkill ? <WorkflowRail
        threadId={threadId}
        turns={turns}
        streaming={streaming}
        agents={agents}
        runs={autoTreeQuery.data?.runs ?? []}
        runLoading={agent?.agent_id === AUTO_AGENT_ID && autoTreeQuery.isLoading}
        runError={autoTreeQuery.isError}
        retryRuns={() => void autoTreeQuery.refetch()}
        onSelectChild={(id) => displayedAutoRunId && openChild(displayedAutoRunId, id)}
        onAvailable={setWorkflowAvailable}
        mobileOpen={workflowOpen}
        onMobileOpenChange={changeWorkflowOpen}
      /> : null}
      {selectedChild ? <AutoSubagentPanel
        selected={selectedChild}
        runs={selectedTreeQuery.data?.runs ?? []}
        agents={agents}
        onClose={closeChild}
        onRefreshTree={() => void selectedTreeQuery.refetch()}
      /> : null}
    </div>
  );
}

const TurnView = memo(function TurnView({
  turn,
  onAsk,
  agent,
  agents,
  autoRunId,
  onOpenChild,
  deepenable,
  deepening,
  onDeepen,
  reconsiderDisabled,
  onFeedback,
  onDecide,
  onSaveArtifact,
  savingContentId,
  savedContentIds,
  onReviewSkill,
}: {
  turn: TranscriptTurn;
  /** Ask a follow-up; set only for the latest finished turn. */
  onAsk?: (question: string) => void;
  agent: Agent;
  agents: Agent[];
  autoRunId: string | null;
  onOpenChild: (rootRunId: string, childRunId: string) => void;
  deepenable: boolean;
  deepening: boolean;
  onDeepen: (turn: TranscriptTurn) => Promise<void>;
  reconsiderDisabled: boolean;
  onFeedback: (turn: TranscriptTurn, feedback: AnswerFeedback) => Promise<void>;
  onDecide: (
    turnId: string,
    call: PendingCall,
    decision: "allow_once" | "allow_session" | "deny",
  ) => Promise<void>;
  onSaveArtifact: (turn: TranscriptTurn, item: SavableContent) => Promise<void>;
  savingContentId: string | null;
  savedContentIds: ReadonlySet<string>;
  onReviewSkill?: (document: string) => void;
}) {
  const running = turn.state === "streaming";
  const visibleSteps: RailStep[] = running && turn.steps.length === 0 && !turn.answer && turn.content.length === 0
    ? [{ id: "starting", kind: "thinking", label: "plan", text: "Starting the agent…", status: "running" }]
    : turn.steps;
  const draft =
    onReviewSkill && turn.state === "done"
      ? extractSkillDraft(turn.answer)
      : null;
  const runContext = useMemo(
    () => ({ database: agent.database_name ?? undefined }),
    [agent.database_name],
  );

  return (
    <div className="flex flex-col gap-3">
      {turn.origin === "reconsider" ? (
        <div className="nova-chat-item flex items-center gap-2 text-sm text-muted-foreground">
          <RefreshCcw aria-hidden="true" className="size-3.5" />
          <span>Reconsidering the previous answer</span>
        </div>
      ) : (
        <div className="nova-chat-item flex flex-col items-end gap-1">
          <div className="max-w-[85%] rounded-2xl bg-foreground px-4 py-2.5 text-sm text-white ring-1 ring-input dark:bg-accent">
            {turn.attachments?.length ? (
              <div className="mb-2 flex flex-wrap gap-1.5" aria-label="Attached files">
                {turn.attachments.map((item, index) => (
                  <span key={`${item.name}-${index}`} className="inline-flex max-w-full items-center gap-1.5 rounded-lg bg-background/20 px-2 py-1 text-xs">
                    {item.mediaType.startsWith("image/") ? (
                      <ImageIcon aria-hidden="true" className="size-3.5 shrink-0" />
                    ) : (
                      <FileText aria-hidden="true" className="size-3.5 shrink-0" />
                    )}
                    <span className="truncate">{item.name}</span>
                  </span>
                ))}
              </div>
            ) : null}
            {turn.question ? <p className="break-words whitespace-pre-wrap">{turn.question}</p> : null}
          </div>
          <UserMessageFooter message={turn.question} createdAt={turn.questionCreatedAt} />
        </div>
      )}

      {agent.agent_id === AUTO_AGENT_ID ? (
        <AutoTurnRail
          rootRunId={autoRunId}
          agents={agents}
          running={running}
          onOpenChild={(childRunId) => autoRunId && onOpenChild(autoRunId, childRunId)}
        />
      ) : (
        <ProcessRail steps={visibleSteps} running={running} revealKey={turn.revealKey} />
      )}

      {turn.pendingConsent ? (
        <ConsentCard
          call={turn.pendingConsent}
          onDecide={(decision) =>
            onDecide(turn.id, turn.pendingConsent as PendingCall, decision)
          }
        />
      ) : null}

      <TurnContent
        turn={turn}
        runContext={runContext}
        running={running}
        onSaveArtifact={(item) => void onSaveArtifact(turn, item)}
        savingContentId={savingContentId}
        savedContentIds={savedContentIds}
      />
      {draft ? (
        <div>
          <Button variant="outline" onClick={() => onReviewSkill?.(draft)}>
            Review and save skill
          </Button>
        </div>
      ) : null}

      {turn.error ? (
        <div
          role="alert"
          className="nova-chat-item flex items-start gap-2 rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          <X aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>{turn.error}</span>
        </div>
      ) : null}

      {turn.state === "cancelled" ? (
        <StopNotice text="Stopped before the answer finished." />
      ) : null}

      {!running && (turn.answer || turn.content.length > 0) ? (
        <AnswerFooter
          answer={turn.answer}
          model={turn.model}
          tokens={turn.tokens}
          inputTokens={turn.inputTokens}
          outputTokens={turn.outputTokens}
          feedback={turn.feedback}
          onFeedback={turn.messageId ? (feedback) => onFeedback(turn, feedback) : undefined}
          onReconsider={deepenable || deepening ? () => void onDeepen(turn) : undefined}
          reconsidering={deepening}
          reconsiderDisabled={reconsiderDisabled}
        />
      ) : null}

      {!running && onAsk && turn.suggestions?.length ? (
        <nav aria-label="Suggested follow-up questions" className="nova-chat-item flex flex-wrap gap-2">
          {turn.suggestions.map((question) => (
            <Button
              key={question}
              type="button"
              variant="outline"
              size="sm"
              className="h-auto max-w-full py-1.5 text-left whitespace-normal"
              onClick={() => onAsk(question)}
            >
              {question}
            </Button>
          ))}
        </nav>
      ) : null}
    </div>
  );
});


function Greeting({ displayName }: { displayName?: string | null }) {
  const hour = new Date().getHours();
  const greeting =
    hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const name = displayName?.trim();
  return (
    <div className="text-left">
      <h1 className="text-3xl leading-tight font-normal text-foreground sm:text-4xl">
        {greeting}
        {name ? `, ${name}` : ""}
      </h1>
      <p className="mt-1 bg-[linear-gradient(90deg,#D04738_0%,#F36B5B_55%,#F59E66_100%)] bg-clip-text text-3xl leading-tight font-medium tracking-[-0.04em] text-transparent sm:text-5xl">
        What insights can I help with?
      </p>
    </div>
  );
}

function SuggestedQuestions({
  questions,
  onPick,
}: {
  questions: string[];
  onPick: (text: string) => void;
}) {
  const [allOpen, setAllOpen] = useState(false);
  const pick = (question: string) => {
    setAllOpen(false);
    onPick(question);
  };

  return (
    <>
      <section aria-label="Suggested questions" className="mx-auto w-full max-w-3xl shrink-0 px-4 pb-4 sm:px-6">
        <div className="mb-2 flex items-center justify-between gap-3">
          <h2 className="text-xs font-medium text-muted-foreground">Suggested questions</h2>
          {questions.length > 4 ? (
            <Button type="button" variant="ghost" size="sm" onClick={() => setAllOpen(true)}>
              View all ({questions.length})
            </Button>
          ) : null}
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {questions.slice(0, 4).map((question) => (
            <button
              key={question}
              type="button"
              title={question}
              onClick={() => pick(question)}
              className="rounded-lg border bg-card px-3 py-2.5 text-left text-sm leading-snug text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="line-clamp-2">{question}</span>
            </button>
          ))}
        </div>
      </section>
      {questions.length > 4 ? (
        <Dialog open={allOpen} onOpenChange={setAllOpen}>
          <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>Suggested questions</DialogTitle>
              <DialogDescription>Choose a question to start a chat.</DialogDescription>
            </DialogHeader>
            <div className="grid gap-2 sm:grid-cols-2">
              {questions.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => pick(question)}
                  className="rounded-lg border bg-card px-3 py-3 text-left text-sm leading-snug text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {question}
                </button>
              ))}
            </div>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
}

/**
 * The composer. It carries the agent picker, so the conversation's agent is
 * chosen where the question is asked rather than in the rail.
 */
