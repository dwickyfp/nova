import { useEffect, useState } from "react";
import {
  BookOpen,
  Check,
  ChevronRight,
  CircleAlert,
  Copy,
  Database,
  Loader2,
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

/**
 * The agentic process, rendered as one collapsible group per turn.
 *
 * This mirrors how Snowflake CoWork shows a run: a single summary header
 * ("Loaded skill and searched...") that opens onto a vertical rail, one row per
 * real step, with the reasoning text tucked inside the row it belongs to. The
 * answer stays clean above it; the work stays available below it.
 *
 * Every row is a real event Nova recorded. There is no invented chain-of-
 * thought: `thinking` frames carry phase labels and redacted status lines, and
 * the group simply gives them a shape a reader can scan.
 */

/** One row on the rail. */
export type RailStep = {
  id: string;
  kind: "thinking" | "tool" | "consent" | "note";
  /** Phase label, tool name, or note kind, used for the icon and the verb. */
  label: string;
  /** The line the user reads. */
  text: string;
  status: ToolCallStatus | "running" | "done";
  /** Redacted SQL or tool preview, disclosed on demand. */
  preview?: string;
  /** Grouping header this step belongs under, when the run has sections. */
  group?: string;
  /** Longer reasoning body, shown when the row is open. */
  body?: string;
  /**
   * What the tool did: a loaded skill's body, or a short result summary.
   * Redacted by the backend. Shown when the row is open.
   */
  detail?: string;
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

/** A step is live while it is running, not yet resolved. */
function isRunning(step: RailStep): boolean {
  return step.status === "running" || step.status === "pending";
}

function isFailed(step: RailStep): boolean {
  return (
    step.status === "failed" ||
    step.status === "denied" ||
    step.status === "cancelled"
  );
}

/**
 * The summary line. It names the work the group contains, in the order it
 * happened, and caps at three verbs so a long run does not produce an essay.
 * A live group says what it is doing now instead of summarising.
 */
export function processSummary(steps: RailStep[], running: boolean): string {
  const live = steps.find(isRunning);
  if (running && live) {
    return live.text;
  }
  const verbs: string[] = [];
  for (const step of steps) {
    const verb =
      step.kind === "tool"
        ? `ran ${step.label}`
        : step.kind === "consent"
          ? "asked for approval"
          : step.kind === "note"
            ? step.label === "plan"
              ? "planned the run"
              : "noted a context change"
            : step.label === "plan"
              ? "planned the run"
              : step.label === "skill"
                ? "loaded a skill"
                : step.label === "observe"
                  ? "read the result"
                  : step.label === "answer"
                    ? "wrote the answer"
                    : "reasoned";
    if (!verbs.includes(verb)) verbs.push(verb);
  }
  if (!verbs.length) return "Worked through the request";
  if (verbs.length === 1) return capitalise(verbs[0]);
  const head = verbs.slice(0, 3).map(capitalise).join(", ");
  return verbs.length > 3 ? `${head}, and more` : head;
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * The collapsible process group. Open while the turn runs, collapsed once it
 * settles, and re-openable at any time. A user who does not care about the
 * trace never has to open it; a user debugging an answer always can.
 */
export function ProcessRail({
  steps,
  running,
  /** Start open. A rail with steps opens, so its detail is never a click away. */
  defaultOpen = true,
  /** Bumped by the parent to force the rail open, e.g. after a decision. */
  revealKey = 0,
}: {
  steps: RailStep[];
  running: boolean;
  defaultOpen?: boolean;
  revealKey?: number;
}) {
  const [open, setOpen] = useState(defaultOpen || running);
  // Once the user touches the header, their choice wins over the auto state.
  const [pinned, setPinned] = useState(false);

  // A reveal (a decision just made) re-opens the rail and releases the pin,
  // because the new row is the thing the user needs to see. An effect, not a
  // render-time state write, so React sees one settled value per commit.
  useEffect(() => {
    if (revealKey <= 0) return;
    setPinned(false);
    setOpen(true);
  }, [revealKey]);

  const expanded = pinned ? open : open || running;
  const failed = steps.some(isFailed);

  if (!steps.length) return null;

  return (
    <Collapsible
      open={expanded}
      onOpenChange={(next) => {
        setPinned(true);
        setOpen(next);
      }}
      className="nova-chat-item"
    >
      <CollapsibleTrigger
        type="button"
        data-testid="process-rail-toggle"
        aria-expanded={expanded}
        className="group flex min-h-9 w-full items-center gap-1.5 rounded-md py-1.5 text-left text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <ChevronRight
          aria-hidden="true"
          className={cn(
            "size-3.5 shrink-0 transition-transform duration-200 ease-out",
            expanded && "rotate-90",
          )}
        />
        {running && !pinned ? (
          <TextShimmer>{processSummary(steps, running)}</TextShimmer>
        ) : (
          <span className={cn(failed && "text-destructive")}>
            {processSummary(steps, running)}
          </span>
        )}
      </CollapsibleTrigger>

      <CollapsibleContent className="CollapsibleContent">
        <div className="mt-1 ml-[7px] border-l border-border pl-4">
          {steps.map((step, i) => (
            <ProcessStepRow
              key={step.id}
              step={step}
              last={i === steps.length - 1}
            />
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function ProcessStepRow({ step, last }: { step: RailStep; last: boolean }) {
  const Icon = stepIcon(step);
  const running = isRunning(step);
  const failed = isFailed(step);
  // Every step that has something to show opens. A reader who opens a step is
  // asking "what did this actually do", so the answer (the skill it read, the
  // statement it ran, the result it saw, the note it left) belongs behind one
  // click, not scattered across different affordances per step kind.
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(step.preview || step.body || step.detail);

  // SQL is evidence, not a hidden implementation detail. When a running tool
  // publishes its generated statement, open that row once so the query is
  // visible during execution. The reader can still collapse it afterwards.
  useEffect(() => {
    if (step.kind === "tool" && running && step.preview) setOpen(true);
  }, [running, step.kind, step.preview]);

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className={cn("relative py-1", !last && "pb-2")}
    >
      <CollapsibleTrigger
        type="button"
        disabled={!hasDetail}
        aria-label={hasDetail ? step.text : undefined}
        className={cn(
          "flex min-h-8 w-full items-center gap-2 rounded-md text-left text-sm",
          hasDetail &&
            "transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
        )}
      >
        <ChevronRight
          aria-hidden="true"
          className={cn(
            "size-3 shrink-0 text-muted-foreground/60 transition-transform duration-200 ease-out",
            hasDetail ? "opacity-100" : "opacity-0",
            open && hasDetail && "rotate-90",
          )}
        />
        <span className="flex size-4 shrink-0 items-center justify-center">
          {running ? (
            <Loader2
              aria-hidden="true"
              className="size-3.5 animate-spin text-muted-foreground"
            />
          ) : failed ? (
            <span
              aria-hidden="true"
              className="size-2 rounded-full bg-destructive"
            />
          ) : (
            <Check
              aria-hidden="true"
              className="size-3.5 text-muted-foreground"
            />
          )}
        </span>
        <Icon
          aria-hidden="true"
          className="size-3.5 shrink-0 text-muted-foreground"
        />
        <span className={cn("truncate", failed && "text-destructive")}>
          {step.text}
        </span>
        {step.kind === "tool" ? (
          <span className="shrink-0 font-mono text-[0.7rem] text-muted-foreground/70">
            {step.label}
          </span>
        ) : null}
      </CollapsibleTrigger>

      {hasDetail ? (
        <CollapsibleContent className="CollapsibleContent">
          <div className="mt-1.5 ml-6 space-y-2">
            {step.preview ? <SqlDisclosure preview={step.preview} /> : null}
            {step.detail ? <ReasoningBody text={step.detail} /> : null}
            {step.body ? <ReasoningBody text={step.body} /> : null}
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
      <pre className="max-h-48 min-w-0 flex-1 overflow-auto rounded-md bg-muted/40 p-2 text-xs leading-relaxed">
        <code className="font-mono">{preview}</code>
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
