import {
  Fragment,
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
  ArrowUp,
  ChevronDown,
  FileText,
  ImageIcon,
  Plus,
  RefreshCcw,
  Square,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import type {
  AssistantEvent,
  ChartBlock as ChartBlockType,
  CitationBlock,
  TableBlock,
  ToolCallView,
} from "@/features/assistant/types";
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
import { AgentMemoryDialog } from "./agent-memory-dialog";
import { AutoSubagentCard, AutoSubagentPanel } from "./auto-subagent-card";
import { AutoTurnRail } from "./auto-turn-rail";
import { rootRunForTurn } from "./auto-run-timeline";
import {
  ACCEPTED_EXTENSIONS,
  MAX_FILES,
  MAX_TOTAL_BYTES,
  MAX_TEXT_TOTAL_BYTES,
  readAttachment,
  type PendingAttachment,
  type SentAttachment,
} from "./studio-attachments";

/** A tool call with the id this conversation uses to resolve it. */
type PendingCall = ToolCallView & { tool_call_id: string };

export type OrderedContent =
  | {
      id: string;
      index: number;
      type: "text";
      text: string;
      complete: boolean;
    }
  | {
      id: string;
      index: number;
      type: "table";
      block: TableBlock;
      complete: true;
    }
  | {
      id: string;
      index: number;
      type: "chart";
      block: ChartBlockType;
      complete: true;
    }
  | {
      id: string;
      index: number;
      type: "citation";
      block: CitationBlock;
      complete: true;
    };

/**
 * One turn in the transcript.
 *
 * A turn is the unit a reader actually thinks in: a question, the work it
 * caused, and the answer that came out. Holding the steps on the turn, rather
 * than flattening them into top-level rows, is what lets the process collapse
 * into a single summary line and stop competing with the answer for attention.
 */
export type TranscriptTurn = {
  id: string;
  question: string;
  attachments?: SentAttachment[];
  /** The ordered work trace: thinking frames, tool calls, context notes. */
  steps: RailStep[];
  /** Answer prose, appended as it streams. */
  answer: string;
  /** Canonical authored output. Higher indexes wait for lower text to seal. */
  content: OrderedContent[];
  /** A pending tool call waiting on the user's decision, if any. */
  pendingConsent: PendingCall | null;
  blocks: {
    tables: { id: string; block: TableBlock }[];
    charts: { id: string; block: ChartBlockType }[];
    citations: CitationBlock[];
  };
  state: "streaming" | "done" | "cancelled" | "error";
  /** Why the turn stopped, when it did not stop cleanly. */
  stopReason?: string;
  error?: string;
  /**
   * A Nova-initiated turn (the reconsider pass), not something the user asked.
   * It renders as a labelled continuation rather than as a user message.
   */
  origin?: "user" | "reconsider";
  /**
   * Bumped when something happens that the user must see in the rail, such as
   * their own approval decision. The rail watches it and opens.
   */
  revealKey?: number;
  /** Total tokens the turn spent, when the provider reported any. */
  tokens?: number;
  /** The model that answered, when it is known. */
  model?: string;
  messageId?: string;
  inputTokens?: number;
  outputTokens?: number;
  feedback?: AnswerFeedback;
};

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
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
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
      if (activeThreadIdRef.current === activeThreadId) setTurns(replayThread(detail.messages));
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
  const abortRef = useRef<AbortController | null>(null);
  const composerRef = useRef<HTMLDivElement | null>(null);
  const composerStartRef = useRef<DOMRect | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const followOutputRef = useRef(true);
  const scrollFrameRef = useRef<number | null>(null);
  const queuedEventsRef = useRef<
    Array<{ turnId: string; event: AssistantEvent }>
  >([]);
  // New chat clears local state before the router necessarily removes the old
  // thread from the URL. Remember that one stale id so it cannot be reloaded.
  const staleThreadAfterNewChatRef = useRef<string | null>(null);
  const activeThreadIdRef = useRef(activeThreadId);
  activeThreadIdRef.current = activeThreadId;
  const eventFrameRef = useRef<number | null>(null);

  // Providers may send one SSE frame per token. Committing React state for
  // every frame reparses Markdown and walks the transcript faster than the
  // browser can paint it. Preserve event order but commit at most once per
  // animation frame, which keeps the stream live without a render storm.
  const flushEvents = useCallback(() => {
    if (eventFrameRef.current !== null) {
      window.cancelAnimationFrame(eventFrameRef.current);
      eventFrameRef.current = null;
    }
    const queued = queuedEventsRef.current.splice(0);
    if (!queued.length) return;
    setTurns((current) =>
      queued.reduce(
        (next, item) => applyEvent(next, item.turnId, item.event),
        current,
      ),
    );
  }, []);

  const queueEvent = useCallback(
    (turnId: string, event: AssistantEvent) => {
      queuedEventsRef.current.push({ turnId, event });
      if (eventFrameRef.current !== null) return;
      eventFrameRef.current = window.requestAnimationFrame(() => {
        eventFrameRef.current = null;
        flushEvents();
      });
    },
    [flushEvents],
  );

  const discardQueuedEvents = useCallback(() => {
    if (eventFrameRef.current !== null) {
      window.cancelAnimationFrame(eventFrameRef.current);
      eventFrameRef.current = null;
    }
    queuedEventsRef.current = [];
  }, []);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      discardQueuedEvents();
    },
    [discardQueuedEvents],
  );

  // A different agent means a different conversation; the sidebar clears the
  // selection for us, so there is nothing to replay.
  useEffect(() => {
    abortRef.current?.abort();
    discardQueuedEvents();
    followOutputRef.current = true;
    setThreadId(null);
    setTurns([]);
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
    setLoadingThread(true);
    abortRef.current?.abort();
    discardQueuedEvents();
    followOutputRef.current = true;
    agentsApi
      .getThread(agent.agent_id, activeThreadId)
      .then((detail) => {
        if (cancelled) return;
        setThreadId(activeThreadId);
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
      return thread.thread_id;
    },
    [activeThreadId, agent, onThreadChange, threadId],
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
      try {
        const thread = await ensureThread(content || pending[0].name);
        if (!thread) throw new Error("Could not start the conversation");
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
          onEvent: (event: AssistantEvent) => queueEvent(turnId, event),
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
          queryKey: ["agents", "threads", agent.agent_id],
        });
        if (agent.agent_id === AUTO_AGENT_ID) {
          void queryClient.invalidateQueries({ queryKey: ["studio", "auto-runs"] });
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
      readingFiles,
      streaming,
      turns.length,
    ],
  );

  const stop = useCallback(() => {
    if (agent?.agent_id === AUTO_AGENT_ID && activeAutoRun) {
      void agentsApi.cancelAutoRun(activeAutoRun.runId).catch(() => {
        toast.error("Could not cancel the Auto run. Check its activity and try again.");
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
          onEvent: (event) => queueEvent(deepenTurnId, event),
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
      {showAutoCard && !selectedChild ? (
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
            showAutoCard && !selectedChild && "xl:pr-[324px]",
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
                {turns.map((turn) => (
                  <TurnView
                    key={turn.id}
                    turn={turn}
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
            showAutoCard && !selectedChild && "xl:pr-[324px]",
          )}
        >
        <Composer
            ref={textareaRef}
            value={input}
            onChange={setInput}
            onKeyDown={onKeyDown}
            onSend={() => void send()}
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
      {selectedChild ? <AutoSubagentPanel
        selected={selectedChild}
        runs={selectedTreeQuery.data?.runs ?? []}
        agents={agents}
        onSelectChild={(childRunId) => openChild(selectedChild.rootRunId, childRunId)}
        onClose={closeChild}
        onRefreshTree={() => void selectedTreeQuery.refetch()}
      /> : null}
    </div>
  );
}

const TurnView = memo(function TurnView({
  turn,
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
        <div className="nova-chat-item flex justify-end">
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
    </div>
  );
});

function AgentPicker({
  agent,
  agents,
  onSelectAgent,
}: {
  agent: Agent | null;
  agents: Agent[];
  onSelectAgent: (id: string) => void;
}) {
  if (agent?.agent_id === SKILL_AUTHOR_ID) {
    return <span className="text-xs text-muted-foreground">Skill creator</span>;
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-8 min-w-0 items-center gap-1 rounded-full border border-border bg-card px-2.5 text-xs text-card-foreground shadow-xs transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <span className="truncate">{agent?.name ?? "Select an agent"}</span>
          <ChevronDown aria-hidden="true" className="size-3 shrink-0" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56 bg-card text-card-foreground">
        <DropdownMenuLabel>Agents</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {agents.length === 0 ? (
          <DropdownMenuItem disabled>No agents available</DropdownMenuItem>
        ) : (
          agents.map((a) => (
            <Fragment key={a.agent_id}>
              <DropdownMenuItem onSelect={() => onSelectAgent(a.agent_id)}>
                {a.agent_id === AUTO_AGENT_ID ? "✦ Auto" : a.name}
              </DropdownMenuItem>
              {a.agent_id === AUTO_AGENT_ID ? <DropdownMenuSeparator /> : null}
            </Fragment>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

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
const Composer = ({
  ref,
  value,
  onChange,
  onKeyDown,
  onSend,
  onStop,
  readingFiles,
  attachments,
  onAddFiles,
  onRemoveAttachment,
  streaming,
  disabled,
  agent,
  agents,
  onSelectAgent,
}: {
  ref: React.RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (v: string) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  onStop: () => void;
  readingFiles: boolean;
  attachments: PendingAttachment[];
  onAddFiles: (files: FileList) => void;
  onRemoveAttachment: (id: string) => void;
  streaming: boolean;
  disabled: boolean;
  agent: Agent | null;
  agents: Agent[];
  onSelectAgent: (id: string) => void;
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  return (
    <div className="mx-auto w-full max-w-3xl">
      {value.startsWith("/") &&
      !value.includes(" ") &&
      CREATE_SKILL_COMMAND.startsWith(value) &&
      value !== CREATE_SKILL_COMMAND ? (
        <Button
          variant="outline"
          className="mb-2 min-h-11 max-w-full whitespace-normal text-left"
          onClick={() => {
            onChange(`${CREATE_SKILL_COMMAND} `);
            ref.current?.focus();
          }}
        >
          {CREATE_SKILL_COMMAND}
        </Button>
      ) : null}
      <div className="flex w-full flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm transition-colors focus-within:border-ring">
        <input
          ref={fileInputRef}
          type="file"
          aria-label="Choose files"
          className="sr-only"
          tabIndex={-1}
          accept={ACCEPTED_EXTENSIONS.join(",")}
          multiple
          onChange={(event) => {
            if (event.currentTarget.files?.length) onAddFiles(event.currentTarget.files);
            event.currentTarget.value = "";
          }}
        />
        {attachments.length ? (
          <div className="flex flex-wrap gap-1.5 px-3 pt-3" aria-label="Files ready to send">
            {attachments.map((item) => (
              <span key={item.id} className="inline-flex max-w-full items-center gap-1 rounded-lg border bg-muted px-2 py-1 text-xs">
                {item.mediaType.startsWith("image/") ? (
                  <ImageIcon aria-hidden="true" className="size-3.5 shrink-0" />
                ) : (
                  <FileText aria-hidden="true" className="size-3.5 shrink-0" />
                )}
                <span className="max-w-40 truncate" title={item.name}>{item.name}</span>
                <button type="button" aria-label={`Remove ${item.name}`} disabled={streaming} onClick={() => onRemoveAttachment(item.id)} className="rounded p-0.5 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40">
                  <X aria-hidden="true" className="size-3.5" />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          aria-label="Message Nova"
          placeholder="Ask a question about your data"
          rows={1}
          disabled={disabled}
          className="max-h-48 w-full resize-none bg-transparent px-4 pt-3.5 pb-2 text-sm leading-relaxed outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
        />
        <div className="flex items-center gap-1 px-2.5 pb-2.5">
          <Button
            variant="ghost"
            size="icon"
            className="size-8 rounded-full border border-border bg-card text-muted-foreground hover:bg-accent"
            disabled={disabled || streaming || readingFiles || attachments.length >= MAX_FILES}
            aria-label="Add file"
            title="Add text, PDF, or image (up to 3 files)"
            onClick={() => fileInputRef.current?.click()}
          >
            <Plus aria-hidden="true" className="size-4" strokeWidth={1} />
          </Button>

          <AgentPicker
            agent={agent}
            agents={agents}
            onSelectAgent={onSelectAgent}
          />
          {agent && agent.agent_id !== SKILL_AUTHOR_ID && agent.agent_id !== AUTO_AGENT_ID ? (
            <AgentMemoryDialog agentId={agent.agent_id} />
          ) : null}

          <div className="flex-1" />
          <button
            type="button"
            onClick={streaming ? onStop : onSend}
            disabled={disabled || readingFiles || (!streaming && !value.trim() && !attachments.length)}
            aria-label={streaming ? "Stop" : "Send"}
            className={cn(
              "flex size-9 items-center justify-center rounded-full transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
              streaming
                ? "bg-secondary text-foreground hover:bg-accent"
                : "bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-40",
            )}
          >
            {streaming ? (
              <Square aria-hidden="true" className="size-3.5 fill-current" />
            ) : (
              <ArrowUp aria-hidden="true" className="size-4" />
            )}
          </button>
        </div>
      </div>
      <p className="mt-2 text-center text-xs text-muted-foreground">
        Enter to send, Shift+Enter for a new line. Type / for commands.
      </p>
    </div>
  );
};

Composer.displayName = "Composer";

/**
 * Rebuild a transcript from a persisted thread.
 *
 * The live transcript is built from SSE frames; this rebuilds the same shape
 * from the trace the loop recorded, so a reopened conversation shows the work
 * that produced each answer instead of the answer alone. A user message opens a
 * turn; the assistant message that follows fills it.
 *
 * Anything the stored trace cannot express is simply absent, never invented: a
 * turn recorded before a step kind existed renders with fewer rows, not with
 * placeholder ones.
 */
export function replayThread(messages: AgentMessage[]): TranscriptTurn[] {
  const turns: TranscriptTurn[] = [];
  let open: TranscriptTurn | null = null;

  for (const message of messages) {
    if (message.role === "user") {
      open = {
        id: message.message_id,
        question: message.content,
        attachments: message.attachments?.map((item) => ({
          name: item.name,
          sizeBytes: item.size_bytes,
          mediaType: item.media_type ?? "text/plain",
        })),
        steps: [],
        answer: "",
        content: [],
        pendingConsent: null,
        blocks: { tables: [], charts: [], citations: [] },
        state: "done",
      };
      turns.push(open);
      continue;
    }
    if (message.role !== "assistant") continue;
    if (!open) {
      // A stored assistant turn with no preceding question: a legacy or
      // truncated thread. It still opens as its own turn.
      open = {
        id: message.message_id,
        question: "",
        steps: [],
        answer: "",
        content: [],
        pendingConsent: null,
        blocks: { tables: [], charts: [], citations: [] },
        state: "done",
      };
      turns.push(open);
    }
    open.answer = message.content;
    open.state = "done";
    open.tokens = message.total_tokens ?? undefined;
    open.messageId = message.message_id;
    open.feedback = message.feedback ?? null;
    open.inputTokens = message.prompt_tokens ?? undefined;
    open.outputTokens = message.completion_tokens ?? undefined;
    open.model = message.model_name ?? undefined;
    let restoredText = false;

    for (const step of message.steps ?? []) {
      switch (step.kind) {
        case "reasoning":
          open.steps.push({
            id: uniqueStepId(open.steps, `think-${step.phase}`),
            kind: "thinking",
            label: step.phase,
            text: step.text,
            status: "done",
            body: step.text,
          });
          break;
        case "tool":
          open.steps.push({
            id: step.tool_call_id || `${step.name}-${open.steps.length}`,
            kind: "tool",
            label: step.name,
            skillName:
              step.name === "load_skill" ? step.arguments?.name : undefined,
            text:
              step.name === "load_skill"
                ? describeStatus(step.name, step.status, step.arguments?.name)
                : step.status_text || describeTool(step.name),
            status: step.status === "failed" ? "failed" : "done",
            preview: step.name === "load_skill" ? undefined : step.preview || undefined,
            detail: step.name === "load_skill" ? undefined : step.detail || undefined,
          });
          break;
        case "text": {
          const index = step.content_index;
          open.content.push({
            id: step.content_id || `${open.id}-text-${index}`,
            index,
            type: "text",
            text: step.text,
            complete: true,
          });
          restoredText = true;
          break;
        }
        case "table":
          {
            const id =
              step.content_id ||
              `${open.id}-table-${open.blocks.tables.length}`;
            const block = {
              tool_call_id: step.tool_call_id,
              title: step.title,
              columns: step.columns,
              rows: step.rows,
            };
            open.blocks.tables.push({ id, block });
            open.content.push({
              id,
              index:
                step.content_index ?? nextLegacyArtifactIndex(open.content),
              type: "table",
              block,
              complete: true,
            });
          }
          break;
        case "chart":
          {
            const id =
              step.content_id ||
              `${open.id}-chart-${open.blocks.charts.length}`;
            const block = {
              tool_call_id: step.tool_call_id,
              chart_spec: step.chart_spec,
            };
            open.blocks.charts.push({ id, block });
            open.content.push({
              id,
              index:
                step.content_index ?? nextLegacyArtifactIndex(open.content),
              type: "chart",
              block,
              complete: true,
            });
          }
          break;
        case "citation":
          open.blocks.citations.push(...step.citations);
          for (const citation of step.citations) {
            open.content.push({
              id:
                step.content_id || `${open.id}-citation-${open.content.length}`,
              index:
                step.content_index ?? nextLegacyArtifactIndex(open.content),
              type: "citation",
              block: citation,
              complete: true,
            });
          }
          break;
        default:
          // `answer` and `context` carry no row of their own.
          break;
      }
    }
    if (!restoredText && message.content) {
      open.content.push({
        id: `${open.id}-text-0`,
        index: 0,
        type: "text",
        text: message.content,
        complete: true,
      });
    }
  }

  return turns;
}

function patchTurn(
  turns: TranscriptTurn[],
  turnId: string,
  patch: (turn: TranscriptTurn) => TranscriptTurn,
): TranscriptTurn[] {
  return turns.map((turn) => (turn.id === turnId ? patch(turn) : turn));
}

/**
 * Return the contiguous authored prefix that is safe to display.
 *
 * The current block is visible while it streams. A higher index is visible
 * only after every preceding block exists and is sealed. This is the client
 * side of the ordering barrier: a table at index 1 waits for text at index 0,
 * while a table intentionally authored at index 0 appears immediately.
 */
let blockSeq = 0;
function nextBlockId(): string {
  blockSeq += 1;
  return `block-${blockSeq}`;
}

/**
 * A rail-step id that no step in `steps` already holds.
 *
 * Two rows must never share a key: React would treat them as one and drop or
 * duplicate a row, and a settled `act` followed by a later `act` is a real
 * sequence, not a repeat. The base id is used when free, so the common single
 * occurrence keeps its stable, readable name; a suffix is appended only on a
 * collision.
 */
function uniqueStepId(steps: RailStep[], base: string): string {
  const taken = new Set(steps.map((step) => step.id));
  if (!taken.has(base)) return base;
  let suffix = steps.length;
  let candidate = `${base}-${suffix}`;
  while (taken.has(candidate)) {
    suffix += 1;
    candidate = `${base}-${suffix}`;
  }
  return candidate;
}

function nextLegacyArtifactIndex(content: OrderedContent[]): number {
  const highestArtifact = content.reduce(
    (highest, item) =>
      item.type === "text" ? highest : Math.max(highest, item.index),
    0,
  );
  return highestArtifact + 1;
}

/**
 * Fold one SSE event into the turn it belongs to.
 *
 * The rules that matter:
 *
 *  - `text_delta` appends to the turn's answer, so it streams in place.
 *  - `thinking` becomes a rail row. A frame that repeats the phase already live
 *    replaces it rather than stacking, because the loop re-announces `act` on
 *    every iteration and a row per iteration would bury the real steps.
 *  - `tool_call` both opens a rail row and, when consent is required, raises the
 *    card. The card is what the turn is waiting on; the row records it either way.
 *  - a status frame updates its row in place, matched by tool call id.
 *  - result blocks are keyed so React keeps them stable across re-renders.
 */
export function applyEvent(
  turns: TranscriptTurn[],
  turnId: string,
  event: AssistantEvent,
): TranscriptTurn[] {
  if (event.type === "role_changed") {
    return turns
      .filter((turn) => turn.id === turnId)
      .map((turn) => ({
        ...turn,
        answer: "",
        content: [],
        steps: [],
        pendingConsent: null,
        blocks: { tables: [], charts: [], citations: [] },
      }));
  }
  return patchTurn(turns, turnId, (turn) => {
    switch (event.type) {
      case "plan": {
        const plan: RailStep = {
          id: "execution-plan",
          kind: "note",
          label: "plan",
          text: `Planned ${event.steps.length} step${event.steps.length === 1 ? "" : "s"}`,
          status: "done",
          body: event.steps
            .map((step, index) => `${index + 1}. ${step.text}`)
            .join("\n"),
        };
        const existing = turn.steps.findIndex((step) => step.id === plan.id);
        return {
          ...turn,
          steps:
            existing === -1
              ? [...turn.steps, plan]
              : turn.steps.map((step, index) =>
                  index === existing ? plan : step,
                ),
        };
      }

      case "thinking": {
        let existing = -1;
        for (let index = turn.steps.length - 1; index >= 0; index -= 1) {
          const step = turn.steps[index];
          if (
            step.kind === "thinking" &&
            step.label === event.phase &&
            step.status === "running"
          ) {
            existing = index;
            break;
          }
        }
        const id =
          existing === -1
            ? uniqueStepId(turn.steps, `think-${event.phase}`)
            : turn.steps[existing].id;
        const row: RailStep = {
          id,
          kind: "thinking",
          label: event.phase,
          text: event.text,
          status: event.status,
          // Every phase carries its own text as the row's body, so any step can
          // be opened to read what it did. The `act` row is the only one the
          // loop re-announces with real narration, but a plan, a skill load and
          // an observation each say something worth keeping.
          body: event.text,
        };
        const steps =
          existing === -1
            ? [...turn.steps, row]
            : turn.steps.map((step, i) =>
                i === existing ? { ...row, body: step.body ?? row.body } : step,
              );
        // A later phase means the earlier one is finished. The loop announces
        // ``act`` on every iteration without closing it, so an unclosed row would
        // otherwise keep its spinner forever once the turn settles.
        const PHASE_ORDER = ["plan", "skill", "act", "observe", "answer"];
        const rank = (step: RailStep) =>
          step.kind === "thinking" ? PHASE_ORDER.indexOf(step.label) : -1;
        const current = rank(row);
        const settled = steps.map((step) =>
          step.kind === "thinking" &&
          step.status === "running" &&
          current > -1 &&
          rank(step) > -1 &&
          rank(step) < current
            ? { ...step, status: "done" as const }
            : step,
        );
        return { ...turn, steps: settled };
      }

      case "tool_call": {
        const { payload } = event;
        const row: RailStep = {
          id: payload.tool_call_id,
          kind: "tool",
          label: payload.tool_name,
          skillName: payload.skill_name || undefined,
          text:
            payload.tool_name === "load_skill"
              ? describeStatus(payload.tool_name, payload.status, payload.skill_name)
              : describeTool(payload.tool_name),
          status: payload.status,
          preview:
            payload.tool_name === "load_skill"
              ? undefined
              : payload.sql_preview || undefined,
        };
        const existing = turn.steps.findIndex(
          (s) => s.id === payload.tool_call_id,
        );
        return {
          ...turn,
          steps:
            existing === -1
              ? [...turn.steps, row]
              : turn.steps.map((s, i) => (i === existing ? row : s)),
          // A `pending` call is the one the turn is blocked on.
          pendingConsent:
            payload.status === "pending" ? payload : turn.pendingConsent,
        };
      }

      case "tool_status": {
        const existing = turn.steps.findIndex(
          (step) => step.id === event.tool_call_id,
        );
        // A call that needed no approval never got a `tool_call` frame carrying
        // its preview in older streams; a status frame alone must still produce
        // a row, or the tool and its SQL would vanish from the rail. The raw
        // call id is never shown: it is a provider artifact, not a tool name.
        const steps =
          existing === -1
            ? [
                ...turn.steps,
                {
                  id: event.tool_call_id,
                  kind: "tool" as const,
                  label: "tool",
                  text: describeStatus("tool", event.status),
                  status: event.status,
                },
              ]
            : turn.steps.map((step) =>
                step.id === event.tool_call_id
                  ? {
                      ...step,
                      status: event.status,
                       text: describeStatus(step.label, event.status, step.skillName),
                    }
                  : step,
              );
        const pendingConsent =
          turn.pendingConsent &&
          turn.pendingConsent.tool_call_id === event.tool_call_id &&
          event.status !== "pending"
            ? null
            : turn.pendingConsent;
        return { ...turn, steps, pendingConsent };
      }

      case "tool_progress":
        return {
          ...turn,
          steps: turn.steps.map((step) =>
            step.id === event.tool_call_id
              ? {
                  ...step,
                  text: event.text,
                  preview:
                    step.label === "load_skill"
                      ? undefined
                      : event.sql_preview ?? step.preview,
                  status:
                    event.stage === "query_completed"
                      ? ("done" as const)
                      : ("running" as const),
                }
              : step,
          ),
        };

      case "text_delta": {
        const currentContent = turn.content ?? [];
        const index = event.content_index ?? 0;
        const id = event.content_id ?? "legacy-text-0";
        const existing = currentContent.findIndex(
          (item) =>
            item.id === id || (item.type === "text" && item.index === index),
        );
        const content =
          existing === -1
            ? [
                ...currentContent,
                {
                  id,
                  index,
                  type: "text" as const,
                  text: event.text,
                  complete: false,
                },
              ]
            : currentContent.map((item, itemIndex) =>
                itemIndex === existing && item.type === "text"
                  ? { ...item, text: item.text + event.text }
                  : item,
              );
        return { ...turn, answer: turn.answer + event.text, content };
      }

      case "table": {
        const currentContent = turn.content ?? [];
        const id = event.content_id ?? nextBlockId();
        const index =
          event.content_index ?? nextLegacyArtifactIndex(currentContent);
        return {
          ...turn,
          content: [
            ...currentContent,
            {
              id,
              index,
              type: "table" as const,
              block: event.payload,
              complete: true as const,
            },
          ],
          blocks: {
            ...turn.blocks,
            tables: [...turn.blocks.tables, { id, block: event.payload }],
          },
        };
      }

      case "chart": {
        const currentContent = turn.content ?? [];
        const id = event.content_id ?? nextBlockId();
        const index =
          event.content_index ?? nextLegacyArtifactIndex(currentContent);
        return {
          ...turn,
          content: [
            ...currentContent,
            {
              id,
              index,
              type: "chart" as const,
              block: event.payload,
              complete: true as const,
            },
          ],
          blocks: {
            ...turn.blocks,
            charts: [...turn.blocks.charts, { id, block: event.payload }],
          },
        };
      }

      case "citation": {
        const currentContent = turn.content ?? [];
        const id = event.content_id ?? nextBlockId();
        const index =
          event.content_index ?? nextLegacyArtifactIndex(currentContent);
        return {
          ...turn,
          content: [
            ...currentContent,
            {
              id,
              index,
              type: "citation" as const,
              block: event.payload,
              complete: true as const,
            },
          ],
          blocks: {
            ...turn.blocks,
            citations: [...turn.blocks.citations, event.payload],
          },
        };
      }

      case "content_block_done":
        return {
          ...turn,
          content: (turn.content ?? []).map((item) =>
            item.id === event.content_id || item.index === event.content_index
              ? { ...item, complete: true }
              : item,
          ),
        };

      case "tool_detail":
        return {
          ...turn,
          steps: turn.steps.map((step) =>
            step.id === event.tool_call_id && step.label !== "load_skill"
              ? { ...step, detail: event.text }
              : step,
          ),
        };

      case "error":
        // A denied or failed tool is reported through both an error frame and a
        // status; the rail row already carries it, so the turn only records the
        // message when there is no answer to show alongside it.
        return {
          ...turn,
          state: turn.answer ? turn.state : "error",
          error: turn.answer ? turn.error : event.message,
        };

      case "done":
        return {
          ...turn,
          // The turn is over: nothing on the rail can still be running, whatever
          // a missed close frame left behind. This is the backstop that stops a
          // spinner outliving the answer.
          steps: turn.steps.map((step) =>
            step.status === "running"
              ? { ...step, status: "done" as const }
              : step,
          ),
          content: (turn.content ?? []).map((item) => ({
            ...item,
            complete: true,
          })),
          state:
            turn.state === "error"
              ? "error"
              : event.finish_reason === "cancelled"
                ? "cancelled"
                : turn.state,
          stopReason:
            event.finish_reason === "stop" ? undefined : event.finish_reason,
          tokens: event.total_tokens ?? turn.tokens,
          messageId: event.message_id,
          inputTokens: event.prompt_tokens ?? turn.inputTokens,
          outputTokens: event.completion_tokens ?? turn.outputTokens,
        };

      default:
        return turn;
    }
  });
}

/** A tool name turned into the line a reader understands. */
function describeTool(name: string, skillName?: string | null): string {
  switch (name) {
    case "query_execute":
      return "Ran a SQL query";
    case "load_skill":
      return skillName ? `Loaded a skill: ${skillName}` : "Loaded a skill";
    case "semantic_query":
      return "Queried the semantic model";
    case "semantic_search":
      return "Searched the semantic model";
    case "data_to_chart":
      return "Built a chart";
    default:
      return `Called ${name}`;
  }
}

function describeStatus(
  name: string,
  status: string,
  skillName?: string | null,
): string {
  switch (status) {
    case "running":
      return name === "load_skill" && skillName
        ? `Loading skill: ${skillName}`
        : `Running ${name}`;
    case "done":
      return describeTool(name, skillName);
    case "failed":
      return `${name} failed`;
    case "denied":
      return `${name} was denied`;
    case "cancelled":
      return `${name} was cancelled`;
    default:
      return describeTool(name);
  }
}
