import { useClientDaypart } from "./use-client-daypart";
import { threadTitle } from "./thread-title";
import { GREETING } from "./greeting";
import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { toast } from "sonner";
import {
  FileText,
  ImageIcon,
  RefreshCcw,
  RotateCcw,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import {
  agentsApi,
  streamAgentTurn,
  resumeAgentTurn,
  studioApi,
  type Agent,
  type AgentMessage,
  type DatavizTurnContext,
} from "@/features/agents/api";
import { ProcessRail, StopNotice, type RailStep } from "./thought-turn";
import { HitlRequest } from "./runtime/hitl/hitl-request";
import type { HitlAnswerBody } from "./runtime/hitl/types";
import { TurnContent } from "./turn-content";
import { flushUiStates } from './intelligent-ui/state';
import type { UiAction } from './intelligent-ui/types';
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
import {
  MAX_FILES,
  MAX_TOTAL_BYTES,
  MAX_TEXT_TOTAL_BYTES,
  readAttachment,
  type PendingAttachment,
} from "./studio-attachments";
import { Composer } from "./studio-composer";
import { useTranscriptStream } from "./use-transcript-stream";
import { rememberLastAgent } from "./default-agent";
import { mergeMissionProjection, type Mission } from "./workflow-api";
import { useAuthStore } from "@/stores/auth-store";
import {
  NATIVE_DASHBOARD_ACTIVITY,
  NATIVE_DASHBOARD_CHANGED,
  type NativeDashboardActivity,
  type NevaState,
} from "@/features/native-dataviz/dashboard/events";
import {
  contentFromParts,
  newTurn,
  patchTurn,
  replayThread,
  settleStream,
  settleTurn,
  stepsFromParts,
  syncTurn,
  turnSegments,
  type StudioEvent,
  type TranscriptTurn,
} from "./studio-transcript";
import { emptyRunState, type HitlPart } from "./runtime/turn-reducer";

function activity(detail: NativeDashboardActivity) {
  window.dispatchEvent(new CustomEvent(NATIVE_DASHBOARD_ACTIVITY, { detail }));
}

export type { OrderedContent, TranscriptTurn } from "./studio-transcript";

/** A News story handed to Studio: its thread, investigating Mission and prompt. */
export type StudioHandoff = { threadId: string; missionId: string; prompt: string };


/**
 * How long Stop waits for the server's closing frame after the run accepted the
 * cancel. The server normally ends the stream itself; past this, the browser
 * stops listening so Stop always ends the turn on screen.
 */
const STOP_GRACE_MS = 3000;

type ActiveRun = { agentId: string; threadId: string; runId: string };

/** What a sent turn needs to be sent again exactly: its text, files and UI action. */
type TurnRequest = { content: string; attachments: PendingAttachment[]; uiAction?: UiAction };

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
  handoff = null,
  noAgents = false,
  variant = "full",
  datavizContext,
}: {
  agent: Agent | null;
  agents: Agent[];
  onSelectAgent: (id: string) => void | Promise<boolean | void>;
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
  /** A News story handed to Studio; applied only in the thread it created. */
  handoff?: StudioHandoff | null;
  /** The catalog loaded and the user has no agent to chat with. */
  noAgents?: boolean;
  /** `panel`: the Dataviz assistant beside the dashboard (narrow, no research). */
  variant?: "full" | "panel";
  /** Read when a message is sent: the dashboard on screen in Dataviz. */
  datavizContext?: () => DatavizTurnContext | null;
}) {
  const queryClient = useQueryClient();
  const selectComposerAgent = async (id: string) => {
    if (streaming || readingFiles || abortRef.current) return;
    try { await flushUiStates(); } catch (error) { toast.error((error as Error).message); return; }
    preserveDraft.current = true;
    try {
      const changed = await onSelectAgent(id);
      if (changed === false) preserveDraft.current = false;
    } catch (error) {
      preserveDraft.current = false;
      toast.error((error as Error).message);
    }
  };
  const preserveDraft = useRef(false);
  // `threadId` records the thread whose transcript is actually loaded. It
  // starts empty even when the URL names a thread, so the load effect below
  // always runs for that thread instead of assuming it is already in hand.
  const [threadId, setThreadId] = useState<string | null>(null);
  const [research, setResearch] = useState<{ agentId: string; runId: string } | null>(null);
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const turnsRef = useRef(turns);
  useLayoutEffect(() => {
    turnsRef.current = turns;
  }, [turns]);
  const [followUpMissionId, setFollowUpMissionId] = useState<string>();
  const securityEpoch = useAuthStore((state) => state.securityEpoch);
  const draftSecurityEpoch = useRef(securityEpoch);
  useEffect(() => {
    if (draftSecurityEpoch.current === securityEpoch) return;
    draftSecurityEpoch.current = securityEpoch;
    preserveDraft.current = false;
  }, [securityEpoch]);
  useEffect(() => setFollowUpMissionId(undefined), [activeThreadId, agent?.agent_id, securityEpoch]);
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
  const directRunRef = useRef<ActiveRun | null>(null);
  const stopRequestedRef = useRef(false);
  // File contents are not kept on the transcript; Retry re-sends them from here.
  const turnRequestsRef = useRef(new Map<string, TurnRequest>());
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [loadingThread, setLoadingThread] = useState(false);
  const [extended, setExtended] = useState(false);
  const [deepenTarget, setDeepenTarget] = useState<string | null>(null);
  const { abortRef, queueEvent, flushEvents, discardQueuedEvents } = useTranscriptStream(setTurns);
  /** Ask the server to cancel a run, then stop listening if it does not close the stream. */
  const cancelActiveRun = useCallback((run: ActiveRun) => {
    const controller = abortRef.current;
    void agentsApi.cancelRun(run.agentId, run.threadId, run.runId).then(() => {
      window.setTimeout(() => {
        if (controller && abortRef.current === controller) controller.abort();
      }, STOP_GRACE_MS);
    }, () => {
      toast.error("Could not cancel the agent run. Try Stop again.");
    });
  }, [abortRef]);
  /** Remember a turn's server run, cancelling it at once when Stop came first. */
  const registerRun = useCallback((run: ActiveRun) => {
    directRunRef.current = run;
    if (stopRequestedRef.current) cancelActiveRun(run);
  }, [cancelActiveRun]);
  const toolStatus = useRef(new Map<string, string>());
  /** part id → tool call id, so status events reach Neva's dashboard pointer. */
  const toolParts = useRef(new Map<string, string>());
  const onTurnEvent = useCallback((turnId: string, event: StudioEvent) => {
    const aux = event.type === "aux" ? event.event : null;
    const workflow = aux as unknown as { type: string; mission?: Mission } | null;
    if (workflow?.type === "mission_updated" && workflow.mission) {
      const mission = workflow.mission;
      queryClient.setQueryData<{ missions: Mission[] }>(
        ["studio", "workflow", useAuthStore.getState().securityEpoch, mission.thread_id],
        (current) => mergeMissionProjection(current, mission, mission.thread_id),
      );
    }
    if (aux?.type === "dashboard_changed") {
      // Native dashboards reload the committed revision Neva just wrote.
      window.dispatchEvent(new CustomEvent(NATIVE_DASHBOARD_CHANGED, {
        detail: { dashboard_id: aux.dashboard_id, revision: aux.revision },
      }));
      activity({ kind: "status", tool_call_id: aux.tool_call_id, state: "saved" });
    }
    // Neva's pointer on the dashboard follows the change it proposes, and
    // whether that change was approved and saved.
    if (event.type === "part.start" && event.part.type === "tool") {
      toolParts.current.set(event.partId, event.part.toolCallId);
      toolStatus.current.set(event.part.toolCallId, "running");
    }
    if (aux?.type === "dashboard_activity") {
      activity({
        kind: "proposed", tool_call_id: aux.tool_call_id, dashboard_id: aux.dashboard_id,
        action: aux.action, widget_ids: aux.widget_ids, page_id: aux.page_id,
        state: toolStatus.current.get(aux.tool_call_id) === "pending" ? "waiting" : "saving",
      });
    }
    if (event.type === "tool.status") {
      const toolCallId = toolParts.current.get(event.partId);
      const state: NevaState | null = event.status === "running" || event.status === "approved" ? "saving"
        : event.status === "failed" ? "failed"
        : event.status === "denied" || event.status === "cancelled" ? "stopped" : null;
      if (toolCallId) toolStatus.current.set(toolCallId, event.status);
      if (state && toolCallId) activity({ kind: "status", tool_call_id: toolCallId, state });
    }
    queueEvent(turnId, event);
  }, [queryClient, queueEvent]);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
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
    turnRequestsRef.current.clear();
    historyRequestRef.current += 1;
    storedMessagesRef.current = [];
    setOlderCursor(null);
    setLoadingOlder(false);
    if (!preserveDraft.current) {
      setInput("");
      setAttachments([]);
    }
    preserveDraft.current = false;
    setDeepenTarget(null);
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
      .then(async (detail) => {
        if (cancelled) return;
        const restored = replayThread(detail.messages);
        const unfinished = restored[restored.length - 1];
        const active = unfinished && !unfinished.messageId
          ? await Promise.resolve().then(() => agentsApi.activeRun(agent.agent_id, activeThreadId)).catch(() => null)
          : null;
        if (cancelled) return;
        setThreadId(activeThreadId);
        storedMessagesRef.current = detail.messages;
        setOlderCursor(detail.next_cursor ?? null);
        setTurns(restored);
        setAttachments([]);
        setDeepenTarget(null);
        if (active?.run_id && unfinished && !unfinished.messageId) {
          const controller = new AbortController();
          abortRef.current = controller;
          directRunRef.current = { agentId: agent.agent_id, threadId: activeThreadId, runId: active.run_id };
          stopRequestedRef.current = false;
          setStreaming(true);
          // The resumed stream replays the run from its first event into a fresh turn.
          setTurns((current) => patchTurn(current, unfinished.id, (turn) => syncTurn({ ...turn, run: emptyRunState(), protocolVersion: 2 })));
          void resumeAgentTurn(agent.agent_id, activeThreadId, active.run_id, {
            signal: controller.signal,
            onEvent: (event) => onTurnEvent(unfinished.id, event),
          }).catch((error) => {
            if (!controller.signal.aborted)
              setTurns((current) => patchTurn(current, unfinished.id, (turn) => settleTurn(turn, "error", (error as Error).message)));
          }).finally(() => {
            if (abortRef.current !== controller) return;
            flushEvents();
            setTurns((current) => settleStream(current, unfinished.id, stopRequestedRef.current));
            setStreaming(false);
            directRunRef.current = null;
            abortRef.current = null;
          });
        }
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
  }, [activeThreadId, agent, discardQueuedEvents, threadId, abortRef, flushEvents, onTurnEvent]);

  // The sidebar's "New" control: drop to a fresh conversation.
  useEffect(() => {
    if (newChatNonce === 0) return;
    staleThreadAfterNewChatRef.current = activeThreadIdRef.current;
    abortRef.current?.abort();
    discardQueuedEvents();
    followOutputRef.current = true;
    setThreadId(null);
    setTurns([]);
    turnRequestsRef.current.clear();
    historyRequestRef.current += 1;
    storedMessagesRef.current = [];
    setOlderCursor(null);
    setLoadingOlder(false);
    setInput("");
    setAttachments([]);
    setDeepenTarget(null);
  }, [discardQueuedEvents, newChatNonce]);

  useEffect(() => {
    if (initialPrompt) {
      setInput(initialPrompt);
      textareaRef.current?.focus();
    }
  }, [initialPrompt, newChatNonce, agent?.agent_id]);

  // A News handoff: declared after the resets above (agent change, Mission
  // follow-up, new chat) so its prompt and Mission win in the handoff thread.
  useEffect(() => {
    if (!handoff || handoff.threadId !== activeThreadId) return;
    setFollowUpMissionId(handoff.missionId);
    setInput(handoff.prompt);
    textareaRef.current?.focus();
  }, [handoff, activeThreadId, agent?.agent_id, securityEpoch]);

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
  // Pin to the newest content before the browser paints, so an opened thread
  // shows its end on the first frame instead of its top and then a jump.
  useLayoutEffect(() => {
    if (!followOutputRef.current) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);
  // Charts, tables and maps finish laying out after the turn renders. While the
  // reader follows the end, keep it pinned as they grow instead of leaving the
  // view short of the bottom.
  useEffect(() => {
    const el = scrollRef.current;
    const content = transcriptRef.current;
    if (!el || !content || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (followOutputRef.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, []);

  // Grow the composer with its text. An empty composer keeps its CSS height:
  // measuring the placeholder before layout settles (a hidden tab, a pane
  // still sizing) left it clipped or several lines tall. Width changes
  // re-measure, since wrapping changes the height.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const fit = () => {
      el.style.height = "";
      if (!el.value || el.clientWidth === 0) return;
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    };
    fit();
    let width = el.clientWidth;
    const observer = new ResizeObserver(() => {
      if (el.clientWidth === width) return;
      width = el.clientWidth;
      fit();
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [input]);

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
      const thread = await agentsApi.createThread(agent.agent_id, threadTitle(title));
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

  const openingConversation = loadingThread || Boolean(
    activeThreadId && activeThreadId !== threadId &&
    activeThreadId !== staleThreadAfterNewChatRef.current,
  );

  const send = useCallback(
    async (override?: string, uiAction?: UiAction, retry?: TurnRequest) => {
      // A retry re-sends an earlier request and leaves the composer's draft alone.
      const content = (retry?.content ?? override ?? input).trim();
      const pending = retry ? retry.attachments : attachments;
      uiAction = retry ? retry.uiAction : uiAction;
      if (openingConversation || (!content && !pending.length) || streaming || readingFiles || abortRef.current || !agent) return;
      try { await flushUiStates(); } catch (error) { toast.error((error as Error).message); return; }
      if (abortRef.current) return;

      const controller = new AbortController();
      abortRef.current = controller;
      // Sending is a request to see the reply, wherever the reader had scrolled.
      followOutputRef.current = true;
      const turnId = nextTurnId();
      turnRequestsRef.current.set(turnId, { content, attachments: pending, uiAction });
      setTurns((prev) => [
        ...prev,
        newTurn({
          id: turnId,
          question: content,
          questionCreatedAt: new Date().toISOString(),
          attachments: pending.map(({ name, sizeBytes, mediaType }) => ({ name, sizeBytes, mediaType })),
          model: agent.model_name ?? undefined,
        }),
      ]);
      if (!retry) {
        setInput("");
        setAttachments([]);
      }
      setStreaming(true);
      stopRequestedRef.current = false;
      setDeepenTarget(null);
      if (variant !== "panel") rememberLastAgent(agent.agent_id);

      let accepted = false;
      let submittedThread: string | null = null;
      try {
        const thread = await ensureThread(content || pending[0].name);
        if (!thread) throw new Error("Could not start the conversation");
        submittedThread = thread;
        // Stop pressed while the conversation was being created: send nothing.
        if (controller.signal.aborted || stopRequestedRef.current) return;
        await streamAgentTurn(agent.agent_id, thread, content, {
          uiAction,
          dataviz: datavizContext?.() ?? null,
          signal: controller.signal,
          role: currentRole,
          continueMissionId: followUpMissionId,
          attachments: pending.map(({ name, content: fileContent, mediaType }) => ({
            name, content: fileContent, media_type: mediaType,
          })),
          onAccepted: () => {
            accepted = true;
            setFollowUpMissionId((current) => current === followUpMissionId ? undefined : current);
          },
          onRunId: (runId) => registerRun({ agentId: agent.agent_id, threadId: thread, runId }),
          onEvent: (event) => onTurnEvent(turnId, event),
        });
      } catch (error) {
        if ((error as Error).name !== "AbortError" && !controller.signal.aborted) {
          if (!accepted && !retry) {
            setInput((current) => current || content);
            setAttachments((current) => current.length ? current : pending);
          }
          setTurns((prev) =>
            patchTurn(prev, turnId, (turn) => settleTurn(turn, "error", (error as Error).message)),
          );
        }
      } finally {
        flushEvents();
        setTurns((prev) => settleStream(prev, turnId, stopRequestedRef.current));
        setStreaming(false);
        directRunRef.current = null;
        abortRef.current = null;
        queryClient.invalidateQueries({
          queryKey: ["agents", "threads"],
        });
        void queryClient.invalidateQueries({ queryKey: ["studio", "workflow"] });
        if (submittedThread && queryClient.getQueryData<{ missions: Mission[] }>(["studio", "workflow", useAuthStore.getState().securityEpoch, submittedThread])?.missions) {
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
      followUpMissionId,
      queryClient,
      queueEvent,
      onTurnEvent,
      readingFiles,
      openingConversation,
      registerRun,
      streaming,
      turns.length,
    ],
  );

  // Turns are memoized; hand them callbacks whose identity survives typing and
  // streaming, or every keystroke and streamed token re-renders every chart.
  const sendRef = useRef(send);
  useLayoutEffect(() => {
    sendRef.current = send;
  }, [send]);
  const sendFromTurn = useCallback(
    (message: string, action?: UiAction) => sendRef.current(message, action),
    [],
  );
  const askFollowUp = useCallback((question: string) => void sendRef.current(question), []);
  const uiContext = useMemo(
    () => (threadId && agent ? { agentId: agent.agent_id, threadId, onAction: sendFromTurn } : undefined),
    [agent, sendFromTurn, threadId],
  );

  // Every run (a sent message, a reconnected run, a reconsider pass) registers
  // its server run here, so Stop cancels whichever one is streaming. A run not
  // yet accepted is cancelled the moment its id arrives.
  const stop = useCallback(() => {
    stopRequestedRef.current = true;
    const run = directRunRef.current;
    if (run) cancelActiveRun(run);
  }, [cancelActiveRun]);

  /**
   * Answer a human-in-the-loop request (an approval, a question, a form or a
   * choice). The request settles from the server's `input.resolved` on the
   * still-open turn stream, so the card collapses when the run has the answer,
   * not when this request returns.
   */
  const answer = useCallback(
    async (turn: TranscriptTurn, part: HitlPart, body: HitlAnswerBody) => {
      const runId = turn.run.runId;
      if (!agent || !threadId || !runId) throw new Error("This request is no longer waiting.");
      const failure = await agentsApi
        .answerInput(agent.agent_id, threadId, runId, part.requestId, body)
        .then(() => null, (cause: unknown) => cause ?? new Error("The answer could not be sent. Try again."));
      if (failure === null) return;
      // A request the run no longer waits for (answered, expired or stopped).
      if ((failure as { status?: number }).status === 404) throw new Error("This request is no longer waiting.");
      throw failure instanceof Error ? failure : new Error("The answer could not be sent. Try again.");
    },
    [agent, threadId],
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
      if (!agent || !threadId || streaming) return;
      // A failed reconsider pass can be retried; otherwise there must be an answer to reconsider.
      if (turn.origin !== "reconsider" && !turn.answer) return;
      setDeepenTarget(turn.id);
      const prompt =
        "Reconsider your previous answer. Work through the reasoning step by step, " +
        "state any assumption you made, name any weakness in the numbers, and give a " +
        "revised conclusion. Do not repeat the original answer verbatim.";
      setStreaming(true);
      stopRequestedRef.current = false;
      const controller = new AbortController();
      abortRef.current = controller;
      const deepenTurnId = nextTurnId();
      setTurns((prev) => [
        ...prev,
        newTurn({
          id: deepenTurnId,
          question: "Reconsider the previous answer, step by step.",
          origin: "reconsider",
          model: agent.model_name ?? undefined,
        }),
      ]);
      try {
        await streamAgentTurn(agent.agent_id, threadId, prompt, {
          signal: controller.signal,
          role: currentRole,
          onRunId: (runId) => registerRun({ agentId: agent.agent_id, threadId, runId }),
          onEvent: (event) => onTurnEvent(deepenTurnId, event),
        });
      } catch (error) {
        if ((error as Error).name !== "AbortError" && !controller.signal.aborted) {
          setTurns((prev) =>
            patchTurn(prev, deepenTurnId, (t) => settleTurn(t, "error", (error as Error).message)),
          );
        }
      } finally {
        flushEvents();
        setTurns((prev) => settleStream(prev, deepenTurnId, stopRequestedRef.current));
        setStreaming(false);
        setDeepenTarget(null);
        directRunRef.current = null;
        abortRef.current = null;
      }
    },
    [agent, abortRef, currentRole, flushEvents, onTurnEvent, registerRun, streaming, threadId],
  );

  /** The request a failed or stopped turn can be sent again with, if it can be. */
  const retryRequest = (turn: TranscriptTurn): TurnRequest | null => {
    const saved = turnRequestsRef.current.get(turn.id);
    if (saved) return saved;
    // A reopened turn kept its text but not its files; without them it is not the same request.
    return turn.question && !turn.attachments?.length
      ? { content: turn.question, attachments: [] }
      : null;
  };
  const retry = useCallback((turn: TranscriptTurn) => {
    if (turn.origin === "reconsider") return void deepen(turn);
    const request = retryRequest(turn);
    if (request) void send(undefined, undefined, request);
  }, [deepen, send]);
  const retryRef = useRef(retry);
  useLayoutEffect(() => {
    retryRef.current = retry;
  }, [retry]);
  const retryFromTurn = useCallback((turn: TranscriptTurn) => retryRef.current(turn), []);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };


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
    agent && agent.agent_id !== SKILL_AUTHOR_ID && !streaming,
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
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col",
        )}
      >
        <div
          ref={scrollRef}
          className={cn(
            "flex min-h-0 w-full flex-1 flex-col overflow-y-auto",
          )}
          onScroll={onTranscriptScroll}
        >
          <div
            ref={transcriptRef}
            className={cn(
              "mx-auto w-full px-4 sm:px-6",
              variant === "panel" ? "px-3 sm:px-4" : 'max-w-3xl',
              welcome ? "my-auto shrink-0 py-6 sm:py-12" : "py-8 sm:py-10",
              // The full Studio chat sits under the floating workspace tabs.
              variant !== "panel" && "pt-16 sm:pt-16",
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
            ) : !agent && noAgents ? (
              <NoAgentState />
            ) : variant === "panel" && (empty || !agent) ? (
              <DatavizGreeting
                displayName={displayName}
                onPick={agent ? (text) => void send(text) : undefined}
              />
            ) : empty || !agent ? (
              <>
                <Greeting displayName={displayName} />
                {!creatingSkill && agent && agent.sample_questions.length > 0 ? (
                  <SuggestedQuestions questions={agent.sample_questions} onPick={(text) => void send(text)} />
                ) : null}
              </>
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
                    uiContext={uiContext}
                    onAsk={index === turns.length - 1 && !streaming ? askFollowUp : undefined}
                    agent={agent}
                    deepenable={
                      extended && turn.state === "done" && Boolean(turn.answer)
                    }
                    deepening={deepenTarget === turn.id}
                    onDeepen={deepen}
                    reconsiderDisabled={streaming}
                    onRetry={
                      index === turns.length - 1 && !streaming &&
                      (turn.state === "error" || turn.state === "cancelled") &&
                      (turn.origin === "reconsider" || retryRequest(turn))
                        ? retryFromTurn
                        : undefined
                    }
                    onFeedback={saveFeedback}
                    onAnswer={answer}
                    onReviewSkill={creatingSkill ? setSkillDraft : undefined}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        <div
          data-testid="studio-composer"
          className={cn(
            "w-full shrink-0 px-4 sm:px-6",
            "pb-6 pt-2",
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
          onResearch={researchable && variant === "full" ? () => void startResearch() : undefined}
          onStop={stop}
          readingFiles={readingFiles}
          attachments={attachments}
          onAddFiles={(files) => void addFiles(files)}
          onRemoveAttachment={(id) => setAttachments((current) => current.filter((item) => item.id !== id))}
            streaming={streaming}
            disabled={!agent}
            sendDisabled={openingConversation}
            agent={agent}
            agents={agents}
            onSelectAgent={(id) => void selectComposerAgent(id)}
            agentPicker={variant !== "panel"}
          />
        </div>
      </div>
      {skillDraft ? (
        <SkillEditor
          initialDocument={skillDraft}
          onClose={() => setSkillDraft(null)}
        />
      ) : null}
      </div>
    </div>
  );
}

const STARTING: RailStep[] = [
  { id: "starting", kind: "thinking", label: "plan", text: "Starting the agent…", status: "running" },
];

/**
 * Shown where the role changed mid-turn. Work done under the previous role is
 * withdrawn, and earlier messages stay hidden because they belong to it.
 */
function RoleChangeNotice({ role }: { role: string }) {
  return (
    <div role="status" className="nova-chat-item flex items-center gap-3 text-xs text-muted-foreground">
      <span aria-hidden="true" className="h-px flex-1 bg-border" />
      <span className="max-w-[80%] text-center">
        Role changed to <span className="font-medium text-foreground">{role}</span>. Earlier messages
        are hidden because they belong to the previous role.
      </span>
      <span aria-hidden="true" className="h-px flex-1 bg-border" />
    </div>
  );
}

const TurnView = memo(function TurnView({
  turn,
  onAsk,
  agent,
  deepenable,
  deepening,
  onDeepen,
  reconsiderDisabled,
  onRetry,
  onFeedback,
  onAnswer,
  onReviewSkill,
  uiContext,
}: {
  turn: TranscriptTurn;
  /** Ask a follow-up; set only for the latest finished turn. */
  onAsk?: (question: string) => void;
  agent: Agent;
  deepenable: boolean;
  deepening: boolean;
  onDeepen: (turn: TranscriptTurn) => Promise<void>;
  reconsiderDisabled: boolean;
  /** Send the same request again as a new turn; set only for the latest failed or stopped turn. */
  onRetry?: (turn: TranscriptTurn) => void;
  onFeedback: (turn: TranscriptTurn, feedback: AnswerFeedback) => Promise<void>;
  onAnswer: (turn: TranscriptTurn, part: HitlPart, body: HitlAnswerBody) => Promise<void>;
  onReviewSkill?: (document: string) => void;
  uiContext?: { agentId: string; threadId: string; onAction: (message: string, action: UiAction) => Promise<void> };
}) {
  const running = turn.state === "streaming";
  const segments = useMemo(() => turnSegments(turn.run.parts), [turn.run.parts]);
  const lastWork = segments.reduce((found, segment, index) => (segment.kind === "work" ? index : found), -1);
  const starting = running && segments.every((segment) => segment.kind === "role_change");
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

      {segments.map((segment, index) =>
        segment.kind === "role_change" ? (
          <RoleChangeNotice key={segment.id} role={segment.activeRole} />
        ) : segment.kind === "hitl" ? (
          <HitlRequest
            key={segment.id}
            part={segment.part}
            live={running}
            onAnswer={(part, body) => onAnswer(turn, part, body)}
          />
        ) : segment.kind === "work" ? (
          <ProcessRail
            key={segment.id}
            steps={stepsFromParts(segment.parts)}
            running={running && index === lastWork}
            revealKey={turn.revealKey}
            stopped={turn.state === "cancelled" && index === lastWork}
          />
        ) : (
          <TurnContent
            key={segment.id}
            turn={turn}
            content={contentFromParts(segment.parts)}
            uiContext={uiContext}
            runContext={runContext}
            running={running}
            datavizThreadId={uiContext?.threadId ?? null}
          />
        ),
      )}
      {starting ? <ProcessRail steps={STARTING} running revealKey={turn.revealKey} /> : null}


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
      {!turn.error && turn.state === "error" ? (
        <StopNotice text="The run did not finish. Review its activity before retrying." />
      ) : null}
      {onRetry ? (
        <div className="nova-chat-item">
          <Button type="button" variant="outline" size="sm" onClick={() => onRetry(turn)}>
            <RotateCcw aria-hidden="true" />
            Retry
          </Button>
        </div>
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


/** The client's daypart, re-read each minute so an open tab crosses noon or 18:00 on time. */
function Greeting({ displayName }: { displayName?: string | null }) {
  const daypart = useClientDaypart();
  const name = displayName?.trim();
  // One heading in two lines that rise a beat apart. The greeting carries the
  // sky of its hour (sunrise, daylight, dusk); the question stays quiet.
  return (
    <h1 className="text-left text-[2rem] leading-[1.15] font-normal tracking-[-0.02em] text-balance sm:text-[2.75rem]">
      <span className="nova-welcome-line block">
        <span className="nova-greeting-ink" data-daypart={daypart}>
          {GREETING[daypart]}
          {name ? `, ${name}` : ""}
        </span>
      </span>
      <span className="nova-welcome-line block text-muted-foreground">
        What would you like to work on?
      </span>
    </h1>
  );
}

function NoAgentState() {
  return (
    <div className="text-left" role="status">
      <h1 className="text-2xl leading-8 font-normal text-foreground">
        You have no agent yet
      </h1>
      <p className="mt-3 max-w-lg text-sm leading-relaxed text-muted-foreground">
        Studio chats through an agent. Create one, or ask an admin to share one with your role.
      </p>
      <Button asChild className="mt-6">
        <Link to="/agents">Go to Agents</Link>
      </Button>
    </div>
  );
}

/**
 * Neva's empty state beside a dashboard: what it can do here, then three
 * starting points. Quiet on purpose; the dashboard is the subject.
 */
function DatavizGreeting({
  onPick,
}: {
  displayName?: string | null;
  onPick?: (text: string) => void;
}) {
  const questions = [
    "What does this dashboard show?",
    "Which region changed the most this month?",
    "Add a chart of revenue by month",
  ];
  return (
    <section aria-label="Dataviz assistant" className="py-2">
      <h1 className="text-base font-medium text-foreground">Work on this dashboard with Neva</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Ask about its numbers, or ask for a chart, filter or layout change. Neva shows each change before saving it.
      </p>
      {onPick ? (
        <div className="mt-4 grid gap-2">
          {questions.map((question) => (
            <button
              key={question}
              type="button"
              onClick={() => onPick(question)}
              className="flex min-h-10 items-center rounded-lg border bg-card px-3 py-2 text-left text-sm text-foreground transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              {question}
            </button>
          ))}
        </div>
      ) : null}
    </section>
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
      {/* Shares the greeting's left edge: no padding of its own inside the transcript column. */}
      <section aria-label="Suggested questions" className="nova-welcome-follow mt-8 w-full sm:mt-10">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {questions.slice(0, 4).map((question) => (
            <button
              key={question}
              type="button"
              title={question}
              onClick={() => pick(question)}
              className="flex min-h-16 items-center rounded-xl border bg-card px-4 py-3 text-left text-sm leading-snug text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="line-clamp-2">{question}</span>
            </button>
          ))}
        </div>
        {questions.length > 4 ? (
          <Button type="button" variant="ghost" size="sm" className="mt-2 -ml-3 text-muted-foreground" onClick={() => setAllOpen(true)}>
            View all ({questions.length})
          </Button>
        ) : null}
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
