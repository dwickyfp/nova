import { processSummary } from "./process-summary";
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { highlightExplorerSql as highlightSql } from "@/features/database-explorer/sql-highlighter";
import { isSqlPreview } from "./tool-preview";
import { formatArtifactSql } from "./artifact-source";
import {
  BookOpen,
  Check,
  ChevronRight,
  CircleAlert,
  Copy,
  Database,
  MessageSquareText,
  Route,
  Search,
  ShieldQuestion,
  Square,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import type { ThinkingPhase, ToolCallStatus } from "@/features/assistant/types";
import { TextShimmer } from "./text-shimmer";
import { formatSpan, isFailedStatus, isLiveStatus, recordedSpan } from "./rail-model";

/**
 * The agentic process, rendered as one collapsible run per turn.
 *
 * A live run opens onto a tree: each recorded step hangs off the rail on its
 * own elbow and the rail grows as steps arrive. When the answer is done the run folds back into one summary line, so the
 * answer stays the thing a reader sees first.
 *
 * Every row is a real event Nova recorded. There is no invented chain-of-
 * thought: `thinking` frames carry phase labels and redacted status lines.
 * Durations are only shown when Nova measured them, either while this view
 * watched the step run or from the times the run journal recorded.
 */

/** One row on the rail. */
export type RailStep = {
  id: string;
  kind: "thinking" | "tool" | "consent" | "note";
  /** Phase label, tool name, or note kind, used for the icon and the verb. */
  label: string;
  /** Name of a loaded skill, when this is a skill tool row. */
  skillName?: string;
  /** The line the user reads. */
  text: string;
  status: ToolCallStatus | "running" | "done";
  /** Redacted SQL or tool preview, disclosed on demand. */
  preview?: string;
  /** Grouping header this step belongs under, when the run has sections. */
  group?: string;
  /** Longer reasoning body, shown when the row is open. */
  body?: string;
  /** A tool result summary, shown when the row is open. */
  detail?: string;
  /** When the run journal recorded this row. */
  at?: string;
};

const PHASE_ICON: Record<ThinkingPhase, LucideIcon> = {
  plan: Route,
  skill: BookOpen,
  act: Search,
  observe: Database,
  answer: MessageSquareText,
};

const TOOL_ICON: Record<string, LucideIcon> = {
  query_execute: Search,
  load_skill: BookOpen,
  semantic_query: Search,
  semantic_search: Search,
  data_to_chart: Route,
  create_agent: Route,
  create_semantic_model: Route,
};

function stepIcon(step: RailStep): LucideIcon {
  if (step.kind === "consent") return ShieldQuestion;
  if (step.kind === "tool") return TOOL_ICON[step.label] ?? Database;
  if (step.kind === "note") return CircleAlert;
  return PHASE_ICON[step.label as ThinkingPhase] ?? Route;
}

/** Shorter than this, a measured span is noise rather than information. */
const MIN_SPAN_MS = 100;
/** How long a finished run stays open so its last marks can settle. */
const SETTLE_MS = 600;
/** The span key for the run as a whole. */
const RUN_KEY = "__run__";

type Span = { start: number; end?: number };
type Spans = Map<string, Span>;

/**
 * Measure what this view watched run: a span opens when a row is first seen
 * live and closes when it stops being live. Rows that were already finished
 * when the view mounted get no span, so a replay never shows invented times.
 */
function useObservedSpans(keys: { id: string; live: boolean }[]): Spans {
  const signature = JSON.stringify(keys.map((key) => [key.id, key.live]));
  const [spans, setSpans] = useState<Spans>(() => {
    const now = performance.now();
    return new Map(keys.filter((key) => key.live).map((key) => [key.id, { start: now }]));
  });
  useEffect(() => {
    const entries = JSON.parse(signature) as [string, boolean][];
    setSpans((previous) => {
      const now = performance.now();
      let next: Spans | null = null;
      for (const [id, live] of entries) {
        const span = previous.get(id);
        if (live && !span) {
          next ??= new Map(previous);
          next.set(id, { start: now });
        } else if (!live && span && span.end === undefined) {
          next ??= new Map(previous);
          next.set(id, { ...span, end: now });
        }
      }
      return next ?? previous;
    });
  }, [signature]);
  return spans;
}

/** A clock that ticks only while something is live. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(performance.now()), 100);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

function spanLabel(span: Span | undefined, now: number, recorded?: number): string | undefined {
  const ms = span ? (span.end ?? Math.max(now, span.start)) - span.start : recorded;
  return ms !== undefined && ms >= MIN_SPAN_MS ? formatSpan(ms) : undefined;
}

type MarkState = "running" | "live" | "done" | "failed";

/**
 * The status mark, Nova's signature on the rail: a ring whose arc turns while
 * a step works and which fills into a check when it finishes. `strong` marks a
 * result (a whole run) in the success colour; ordinary steps
 * settle into a quiet check. `settle` plays the fill once, only for a step the
 * reader watched finish.
 */
function StatusMark({ state, strong = false, settle = false }: { state: MarkState; strong?: boolean; settle?: boolean }) {
  if (state === "running") {
    return (
      <span aria-hidden="true" className="flex size-4 shrink-0 items-center justify-center">
        <svg viewBox="0 0 16 16" className="size-4 animate-spin text-primary motion-reduce:animate-none">
          <circle cx="8" cy="8" r="6" fill="none" stroke="var(--border)" strokeWidth="1.5" />
          <path d="M8 2a6 6 0 0 1 6 6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </span>
    );
  }
  if (state === "live") {
    return (
      <span aria-hidden="true" className="flex size-4 shrink-0 items-center justify-center">
        <span className="nova-live-dot relative size-2 rounded-full bg-primary" />
      </span>
    );
  }
  const failed = state === "failed";
  return (
    <span aria-hidden="true" className="flex size-4 shrink-0 items-center justify-center">
      <svg
        viewBox="0 0 16 16"
        data-mark={state}
        className={cn(
          "size-4",
          settle && "nova-mark-settle",
          failed ? "text-destructive" : strong ? "text-success" : "text-muted-foreground",
        )}
      >
        {strong || failed ? (
          <circle className="nova-mark-disc" cx="8" cy="8" r="7" fill="currentColor" />
        ) : null}
        <path
          className="nova-mark-ink"
          pathLength={1}
          d={failed ? "M5.5 5.5l5 5M10.5 5.5l-5 5" : "M4.75 8.25l2.25 2.25 4.25-4.75"}
          fill="none"
          stroke={strong || failed ? "var(--background)" : "currentColor"}
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

function stepMark(step: RailStep): MarkState {
  if (isLiveStatus(step.status)) return "running";
  return isFailedStatus(step.status) ? "failed" : "done";
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/**
 * Keep one activity summary visible; disclose recorded steps on request.
 */
export function ProcessRail({
  steps,
  running,
  defaultOpen = false,
  /** Bumped by the parent to force the rail open, e.g. after a decision. */
  revealKey = 0,
}: {
  steps: RailStep[];
  running: boolean;
  defaultOpen?: boolean;
  revealKey?: number;
}) {
  // A live run opens so the work is visible while it happens; a replayed one
  // starts as its summary line.
  const [open, setOpen] = useState(defaultOpen || running);
  // Once the user touches the header, their choice wins over the auto state.
  const [pinned, setPinned] = useState(false);
  const [wasRunning, setWasRunning] = useState(running);
  const [settling, setSettling] = useState(false);

  // Follow the run: open when it starts, fold shortly after it finishes. A
  // state adjustment during render, so the first frame of a new run is open.
  if (wasRunning !== running) {
    setWasRunning(running);
    if (running) {
      setSettling(false);
      if (!pinned) setOpen(true);
    } else if (!pinned) {
      setSettling(true);
    }
  }

  useEffect(() => {
    if (!settling) return;
    const timer = window.setTimeout(() => {
      setSettling(false);
      setOpen(false);
    }, SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [settling]);

  // A reveal (a decision just made) re-opens the rail and releases the pin,
  // because the new row is the thing the user needs to see.
  useEffect(() => {
    if (revealKey <= 0) return;
    setPinned(false);
    setOpen(true);
  }, [revealKey]);

  const spanKeys = useMemo(
    () => [
      { id: RUN_KEY, live: running },
      ...steps.map((step) => ({ id: step.id, live: isLiveStatus(step.status) })),
    ],
    [steps, running],
  );
  const spans = useObservedSpans(spanKeys);
  const now = useNow(running);

  if (!steps.length) return null;

  const failed = steps.some((step) => isFailedStatus(step.status));
  const runSpan = spans.get(RUN_KEY);
  const recordedRun = recordedSpan(steps.find((step) => step.at)?.at, [...steps].reverse().find((step) => step.at)?.at);
  const duration = spanLabel(runSpan, now, running ? undefined : recordedRun);
  const scope = plural(steps.length, "step");

  return (
    <Collapsible
      open={open}
      onOpenChange={(next) => {
        setPinned(true);
        setSettling(false);
        setOpen(next);
      }}
      className="nova-chat-item"
      data-running={running || undefined}
    >
      <CollapsibleTrigger
        type="button"
        data-testid="process-rail-toggle"
        aria-expanded={open}
        className="group flex min-h-9 w-full min-w-0 items-center gap-2 rounded-md py-1.5 text-left text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <StatusMark
          state={running ? "live" : failed ? "failed" : "done"}
          strong
          settle={runSpan?.end !== undefined}
        />
        <span className="flex min-w-0 flex-1 items-baseline gap-2">
          {running && open ? (
            <>
              <span className="shrink-0 text-foreground">Working</span>
              <span className="truncate text-xs">{scope}</span>
            </>
          ) : running ? (
            <TextShimmer className="truncate">{processSummary(steps, running)}</TextShimmer>
          ) : (
            <>
              <span className={cn("truncate", failed && "text-destructive")}>
                {processSummary(steps, running)}
              </span>
              <span className="hidden shrink-0 text-xs sm:inline">{scope}</span>
            </>
          )}
        </span>
        {duration ? (
          <span className="shrink-0 font-mono text-xs tabular-nums">{duration}</span>
        ) : null}
        <ChevronRight
          aria-hidden="true"
          className={cn(
            "size-3.5 shrink-0 transition-transform duration-200 ease-out motion-reduce:transition-none",
            open && "rotate-90",
          )}
        />
      </CollapsibleTrigger>

      <CollapsibleContent className="CollapsibleContent">
        <ul aria-label="Run steps" className="nova-rail mt-1 ml-[7px]">
          {steps.map((step, index) => (
            <li
              key={step.id}
              className="nova-rail-item"
              data-live={isLiveStatus(step.status) || undefined}
              style={{ "--i": Math.min(index, 8) } as CSSProperties}
            >
              <StepRow step={step} span={spans.get(step.id)} now={now} />
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}


function StepRow({ step, span, now }: {
  step: RailStep;
  span?: Span;
  now: number;
}) {
  const text = step.text;
  const Icon = stepIcon(step);
  const live = isLiveStatus(step.status);
  const failed = isFailedStatus(step.status);
  const mark = <StatusMark state={stepMark(step)} settle={span?.end !== undefined} />;
  const duration = spanLabel(span, now);
  // Rows with a statement or result can be opened for more detail.
  const [open, setOpen] = useState(false);
  const body =
    step.body?.trim() && step.body.trim() !== step.text.trim()
      ? step.body
      : undefined;
  const detail =
    step.detail?.trim() && step.detail.trim() !== step.text.trim()
      ? step.detail
      : undefined;
  const hasDetail = Boolean(step.preview || body || detail);

  useEffect(() => {
    if (step.kind === "tool" && live && step.preview) setOpen(true);
  }, [live, step.kind, step.preview]);

  const words = live ? (
    <TextShimmer className="min-w-0 truncate">{text}</TextShimmer>
  ) : (
    <span className={cn("min-w-0 truncate", failed ? "text-destructive" : "text-muted-foreground")}>{text}</span>
  );
  const meta = duration ? (
    <span className="ml-auto shrink-0 pl-2 font-mono text-xs tabular-nums text-muted-foreground">{duration}</span>
  ) : null;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger
        type="button"
        disabled={!hasDetail}
        aria-label={hasDetail ? step.text : undefined}
        className={cn(
          "group flex min-h-8 w-full min-w-0 items-center gap-2 rounded-md text-left text-sm",
          hasDetail &&
            "transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
        )}
      >
        {mark}
        <Icon aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
        {words}
        {meta}
        {hasDetail ? (
          <ChevronRight
            aria-hidden="true"
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground transition-transform duration-200 ease-out motion-reduce:transition-none",
              !meta && "ml-auto",
              open && "rotate-90",
            )}
          />
        ) : null}
      </CollapsibleTrigger>

      {hasDetail ? (
        <CollapsibleContent className="CollapsibleContent">
          <div className="mt-1 mb-2 ml-6 min-w-0 space-y-2">
            {step.kind === "tool" ? (
              <p className="font-mono text-xs text-muted-foreground">{step.label}</p>
            ) : null}
            {step.preview ? <ToolPreview preview={step.preview} /> : null}
            {detail ? <ReasoningBody text={detail} /> : null}
            {body ? <ReasoningBody text={body} /> : null}
          </div>
        </CollapsibleContent>
      ) : null}
    </Collapsible>
  );
}

/**
 * The reasoning text, set as prose at reading width rather than in a code box.
 * It is Nova's narration of a step, so it reads like a note, not an artifact.
 */
export function ReasoningBody({ text }: { text: string }) {
  return (
    <p className="border-l border-border pl-3 text-sm leading-relaxed whitespace-pre-wrap text-muted-foreground">
      {text}
    </p>
  );
}

/**
 * What a tool step will do or did: its statement when it ran SQL, otherwise the
 * tool's own sentence, which is not code and must not be highlighted as SQL.
 */
export function ToolPreview({ preview }: { preview: string }) {
  if (isSqlPreview(preview)) return <SqlDisclosure preview={preview} />;
  return (
    <p className="text-sm leading-relaxed break-words text-muted-foreground">{preview}</p>
  );
}

/**
 * The redacted statement a tool ran.
 *
 * Shown directly, not behind a second toggle: the rail is already the collapse
 * mechanism, so a reader who opened the rail asked to see the work. A statement
 * long enough to dominate the answer is bounded and scrollable instead of
 * hidden, because "which query produced this number" is the question the rail
 * exists to answer. The value is already redacted by the backend.
 */
export function SqlDisclosure({ preview }: { preview: string }) {
  const [copied, setCopied] = useState(false);
  // Formatted for reading; Copy keeps the statement exactly as it ran.
  const formatted = useMemo(() => formatArtifactSql(preview), [preview]);
  const highlighted = useMemo(() => highlightSql(formatted), [formatted]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(preview);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">SQL</span>
        <button
          type="button"
          onClick={() => void copy()}
          className="flex min-h-7 items-center gap-1 rounded px-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          aria-label="Copy SQL"
        >
          {copied ? (
            <Check aria-hidden="true" className="size-3" />
          ) : (
            <Copy aria-hidden="true" className="size-3" />
          )}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre className="sql-code-block hljs max-h-48 min-w-0 flex-1 overflow-auto rounded-md bg-muted/40 p-2 text-xs leading-relaxed">
        {highlighted ? (
          <code
            className="font-mono"
            // highlight.js emits only spans with token class names, so the
            // markup is inert.
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        ) : (
          <code className="font-mono">{formatted}</code>
        )}
      </pre>
    </div>
  );
}

/** A terminated run, so the rail can explain why there is no answer. */
export function StopNotice({ text }: { text: string }) {
  return (
    <div className="nova-chat-item flex items-center gap-2 text-sm text-muted-foreground">
      <Square aria-hidden="true" className="size-3 fill-current" />
      <span>{text}</span>
    </div>
  );
}
