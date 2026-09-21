import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowUp,
  ChevronDown,
  Paperclip,
  RefreshCcw,
  Square,
  Workflow,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { Markdown } from "@/features/assistant/markdown";
import type {
  AssistantEvent,
  ChartBlock as ChartBlockType,
  CitationBlock,
  TableBlock,
  ToolCallView,
} from "@/features/assistant/types";
import {
  agentsApi,
  streamAgentTurn,
  studioApi,
  type Agent,
  type AgentMessage,
} from "@/features/agents/api";
import { TextShimmer } from "./text-shimmer";
import { ProcessRail, StopNotice, type RailStep } from "./thought-turn";
import { CitationList, ResultChart, ResultTable } from "./result-cards";
import { ConsentCard } from "./consent-card";

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
}) {
  const queryClient = useQueryClient();
  // `threadId` records the thread whose transcript is actually loaded. It
  // starts empty even when the URL names a thread, so the load effect below
  // always runs for that thread instead of assuming it is already in hand.
  const [threadId, setThreadId] = useState<string | null>(null);
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [loadingThread, setLoadingThread] = useState(false);
  const [extended, setExtended] = useState(false);
  const [deepenTarget, setDeepenTarget] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const followOutputRef = useRef(true);
  const scrollFrameRef = useRef<number | null>(null);
  const queuedEventsRef = useRef<
    Array<{ turnId: string; event: AssistantEvent }>
  >([]);
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
    setInput("");
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
    if (!agent || !activeThreadId || activeThreadId === threadId) return;
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
        setDeepenTarget(null);
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
    abortRef.current?.abort();
    discardQueuedEvents();
    followOutputRef.current = true;
    setThreadId(null);
    setTurns([]);
    setInput("");
    setDeepenTarget(null);
  }, [discardQueuedEvents, newChatNonce]);

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

  const ensureThread = useCallback(
    async (title: string): Promise<string | null> => {
      if (!agent) return null;
      if (threadId) return threadId;
      // A thread the URL selected may still be loading. Sending into it must
      // continue that conversation, not silently start a second one.
      if (activeThreadId) return activeThreadId;
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

  const send = useCallback(
    async (override?: string) => {
      const content = (override ?? input).trim();
      if (!content || streaming || !agent) return;
      const thread = await ensureThread(content);
      if (!thread) return;

      const turnId = nextTurnId();
      setTurns((prev) => [
        ...prev,
        {
          id: turnId,
          question: content,
          steps: [],
          answer: "",
          content: [],
          pendingConsent: null,
          blocks: { tables: [], charts: [], citations: [] },
          state: "streaming",
        },
      ]);
      setInput("");
      setStreaming(true);
      setDeepenTarget(null);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamAgentTurn(agent.agent_id, thread, content, {
          signal: controller.signal,
          role: currentRole,
          onEvent: (event: AssistantEvent) => queueEvent(turnId, event),
        });
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
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
      }
    },
    [
      agent,
      currentRole,
      ensureThread,
      flushEvents,
      input,
      queryClient,
      queueEvent,
      streaming,
    ],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    flushEvents();
    setStreaming(false);
    setTurns((prev) =>
      prev.map((turn) =>
        turn.state === "streaming" ? { ...turn, state: "cancelled" } : turn,
      ),
    );
  }, [flushEvents]);

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

  // Whether an answer offers a reconsider pass is a preference, not a
  // per-message control; it is set in Settings and read here.
  useEffect(() => {
    studioApi.settings().then(
      (settings) =>
        setExtended(Boolean(settings?.preferences?.extended_thinking)),
      () => undefined,
    );
  }, []);

  const empty = turns.length === 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-y-auto"
        onScroll={onTranscriptScroll}
      >
        <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 sm:py-10">
          {!agent ? (
            <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
              No agent is available. Create one under AI &amp; ML &gt; Agents to
              use Studio.
            </div>
          ) : loadingThread && empty ? (
            <p className="pt-20 text-center text-sm text-muted-foreground">
              Opening the conversation
            </p>
          ) : empty ? (
            <Greeting
              agent={agent}
              displayName={displayName}
              onPick={(text) => void send(text)}
            />
          ) : (
            <div className="flex flex-col gap-8">
              {turns.map((turn) => (
                <TurnView
                  key={turn.id}
                  turn={turn}
                  agent={agent}
                  deepenable={
                    extended && turn.state === "done" && Boolean(turn.answer)
                  }
                  deepening={deepenTarget === turn.id}
                  onDeepen={deepen}
                  onDecide={decide}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="px-4 pb-6 pt-2 sm:px-6">
        <Composer
          ref={textareaRef}
          value={input}
          onChange={setInput}
          onKeyDown={onKeyDown}
          onSend={() => void send()}
          onStop={stop}
          streaming={streaming}
          disabled={!agent}
          agent={agent}
          agents={agents}
          onSelectAgent={onSelectAgent}
        />
      </div>
    </div>
  );
}

const TurnView = memo(function TurnView({
  turn,
  agent,
  deepenable,
  deepening,
  onDeepen,
  onDecide,
}: {
  turn: TranscriptTurn;
  agent: Agent;
  deepenable: boolean;
  deepening: boolean;
  onDeepen: (turn: TranscriptTurn) => Promise<void>;
  onDecide: (
    turnId: string,
    call: PendingCall,
    decision: "allow_once" | "allow_session" | "deny",
  ) => Promise<void>;
}) {
  const running = turn.state === "streaming";
  const ordered = useMemo(() => visibleContent(turn.content), [turn.content]);
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
          <div className="max-w-[85%] rounded-2xl bg-accent px-4 py-2.5 text-sm text-accent-foreground ring-1 ring-input">
            <p className="break-words whitespace-pre-wrap">{turn.question}</p>
          </div>
        </div>
      )}

      <ProcessRail
        steps={turn.steps}
        running={running}
        revealKey={turn.revealKey}
      />

      {turn.pendingConsent ? (
        <ConsentCard
          call={turn.pendingConsent}
          onDecide={(decision) =>
            onDecide(turn.id, turn.pendingConsent as PendingCall, decision)
          }
        />
      ) : null}

      {ordered.length ? (
        <div className="flex flex-col gap-3" data-testid="ordered-response">
          {ordered.map((item, itemIndex) => {
            if (item.type === "text") {
              return (
                <div key={item.id} className="nova-chat-item min-w-0 text-sm">
                  <Markdown runContext={runContext}>
                    {item.text}
                  </Markdown>
                  {running &&
                  !item.complete &&
                  itemIndex === ordered.length - 1 ? (
                    <TextShimmer className="mt-1">Writing</TextShimmer>
                  ) : null}
                </div>
              );
            }
            if (item.type === "table") {
              return <ResultTable key={item.id} block={item.block} />;
            }
            if (item.type === "chart") {
              return <ResultChart key={item.id} block={item.block} />;
            }
            return <CitationList key={item.id} citations={[item.block]} />;
          })}
        </div>
      ) : turn.answer ? (
        <div className="nova-chat-item min-w-0 text-sm">
          <Markdown runContext={runContext}>
            {turn.answer}
          </Markdown>
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

      {/* How much the turn cost, and which model answered. Shown only when the
          provider actually reported it, so it is never an invented figure. */}
      {turn.tokens || turn.model ? (
        <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
          {turn.model ? <span>{turn.model}</span> : null}
          {turn.tokens ? (
            <span className="tabular-nums">{turn.tokens} tokens</span>
          ) : null}
        </p>
      ) : null}

      {deepenable || deepening ? (
        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="rounded-full"
            disabled={deepening}
            onClick={() => void onDeepen(turn)}
          >
            <RefreshCcw aria-hidden="true" className="size-3.5" />
            {deepening ? "Reconsidering" : "Reconsider this answer"}
          </Button>
        </div>
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
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex min-w-0 items-center gap-1 rounded-full px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <Workflow aria-hidden="true" className="size-3.5 shrink-0" />
          <span className="truncate">{agent?.name ?? "Select an agent"}</span>
          <ChevronDown aria-hidden="true" className="size-3 shrink-0" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuLabel>Agents</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {agents.length === 0 ? (
          <DropdownMenuItem disabled>No agents available</DropdownMenuItem>
        ) : (
          agents.map((a) => (
            <DropdownMenuItem
              key={a.agent_id}
              onSelect={() => onSelectAgent(a.agent_id)}
            >
              {a.name}
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Greeting({
  agent,
  displayName,
  onPick,
}: {
  agent: Agent;
  displayName?: string | null;
  onPick: (text: string) => void;
}) {
  const hour = new Date().getHours();
  const greeting =
    hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const name = displayName?.trim();
  return (
    <div className="flex min-h-[52vh] flex-col justify-center pb-10 text-left sm:min-h-[56vh]">
      <h1 className="text-3xl leading-tight font-medium tracking-[-0.035em] text-foreground sm:text-5xl">
        {greeting}
        {name ? `, ${name}` : ""}
      </h1>
      <p className="mt-1 bg-[linear-gradient(90deg,#D04738_0%,#F36B5B_55%,#F59E66_100%)] bg-clip-text text-3xl leading-tight font-medium tracking-[-0.04em] text-transparent sm:text-5xl">
        What insights can I help with?
      </p>
      {agent.description ? (
        <p className="mt-6 max-w-xl text-sm leading-relaxed text-muted-foreground">
          {agent.description}
        </p>
      ) : null}
      {agent.sample_questions.length ? (
        <div className="mt-6 flex flex-wrap gap-2">
          {agent.sample_questions.map((question) => (
            <button
              key={question}
              type="button"
              onClick={() => onPick(question)}
              className="rounded-full border bg-card px-4 py-2 text-sm text-foreground transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              {question}
            </button>
          ))}
        </div>
      ) : null}
    </div>
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
  streaming: boolean;
  disabled: boolean;
  agent: Agent | null;
  agents: Agent[];
  onSelectAgent: (id: string) => void;
}) => {
  return (
    <div className="mx-auto w-full max-w-3xl">
      <div className="flex w-full flex-col overflow-hidden rounded-2xl border border-border bg-muted/60 shadow-sm backdrop-blur transition-colors focus-within:border-ring focus-within:bg-muted">
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask a question about your data"
          rows={1}
          disabled={disabled}
          className="max-h-48 w-full resize-none bg-transparent px-4 pt-3.5 pb-2 text-sm leading-relaxed outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
        />
        <div className="flex items-center gap-1 px-2.5 pb-2.5">
          <span
            title="Attachments are not supported yet"
            className="inline-flex"
          >
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-full text-muted-foreground"
              disabled
              aria-label="Attachments are not supported yet"
            >
              <Paperclip aria-hidden="true" className="size-4" />
            </Button>
          </span>

          <AgentPicker
            agent={agent}
            agents={agents}
            onSelectAgent={onSelectAgent}
          />

          <div className="flex-1" />
          <button
            type="button"
            onClick={streaming ? onStop : onSend}
            disabled={disabled || (!streaming && !value.trim())}
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
        Enter to send, Shift+Enter for a new line
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
    open.model = message.model_name ?? undefined;
    let restoredText = false;

    for (const step of message.steps ?? []) {
      switch (step.kind) {
        case "reasoning":
          open.steps.push({
            id: `think-${step.phase}`,
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
            text: step.status_text || describeTool(step.name),
            status: step.status === "failed" ? "failed" : "done",
            preview: step.preview || undefined,
            detail: step.detail || undefined,
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
export function visibleContent(content: OrderedContent[]): OrderedContent[] {
  const ordered = [...content].sort((a, b) => a.index - b.index);
  const visible: OrderedContent[] = [];
  let expected = 0;
  for (const item of ordered) {
    if (item.index !== expected) break;
    visible.push(item);
    expected += 1;
    if (!item.complete) break;
  }
  return visible;
}

let blockSeq = 0;
function nextBlockId(): string {
  blockSeq += 1;
  return `block-${blockSeq}`;
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
            ? turn.steps.some(
                (step) =>
                  step.kind === "thinking" && step.label === event.phase,
              )
              ? `think-${event.phase}-${turn.steps.length}`
              : `think-${event.phase}`
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
          text: describeTool(payload.tool_name),
          status: payload.status,
          preview: payload.sql_preview || undefined,
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
                      text: describeStatus(step.label, event.status),
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
                  preview: event.sql_preview ?? step.preview,
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
            step.id === event.tool_call_id
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
        };

      default:
        return turn;
    }
  });
}

/** A tool name turned into the line a reader understands. */
function describeTool(name: string): string {
  switch (name) {
    case "query_execute":
      return "Ran a SQL query";
    case "load_skill":
      return "Loaded a skill";
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

function describeStatus(name: string, status: string): string {
  switch (status) {
    case "running":
      return `Running ${name}`;
    case "done":
      return describeTool(name);
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
