import { useState } from "react";
import { ChevronRight, Loader2, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import type { TranscriptMessage } from "./use-assistant-transcript";

export type ActivityTraceProps = {
  message: TranscriptMessage;
};

/**
 * The transparent agentic trace for one turn, collapsed behind a single row by
 * default so it never crowds a long reply. Expanding reveals the plan and the
 * steps the assistant took. A live turn shows a spinner and opens itself so the
 * work is visible while it happens; a settled trace stays closed until asked.
 *
 * Every line is Nova's own phase label — no model chain-of-thought is shown.
 */
export function ActivityTrace({ message }: ActivityTraceProps) {
  const steps = message.activity_steps ?? [];
  const plan = message.activity_plan ?? [];
  const running = steps.some((step) => step.status === "running");

  // Open while the turn is live, closed once it settles. A user toggle wins
  // afterward so the panel does not fight them.
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? running;

  if (steps.length === 0 && plan.length === 0) return null;

  const summary = running
    ? "Working"
    : `Thought for ${steps.length} step${steps.length === 1 ? "" : "s"}`;

  return (
    <div
      data-role="activity"
      className="rounded-lg border border-dashed bg-surface-1 text-xs text-muted-foreground"
    >
      <button
        type="button"
        onClick={() => setOverride(!open)}
        aria-expanded={open}
        className={cn(
          "flex w-full items-center gap-1.5 rounded-lg px-3 py-2 text-left",
          "hover:bg-surface-2 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
        )}
      >
        <ChevronRight
          aria-hidden="true"
          className={cn(
            "size-3.5 shrink-0 transition-transform",
            open && "rotate-90",
          )}
        />
        {running ? (
          <Loader2
            aria-hidden="true"
            className="size-3.5 shrink-0 animate-spin text-info-strong"
          />
        ) : (
          <Sparkles
            aria-hidden="true"
            className="size-3.5 shrink-0 text-info-strong"
          />
        )}
        <span className="truncate font-medium text-foreground">{summary}</span>
      </button>

      {open ? (
        <div className="flex flex-col gap-2 border-t px-3 py-2">
          {plan.length > 0 ? (
            <ol className="flex flex-col gap-0.5">
              {plan.map((step) => (
                <li key={step.id} className="flex items-center gap-1.5">
                  <StepMark status={step.status} />
                  <span
                    className={cn(step.status === "done" && "text-foreground")}
                  >
                    {step.text}
                  </span>
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function StepMark({ status }: { status: "pending" | "running" | "done" }) {
  if (status === "running") {
    return (
      <Loader2
        aria-hidden="true"
        className="size-3 shrink-0 animate-spin text-info-strong"
      />
    );
  }
  if (status === "done") {
    return (
      <span aria-hidden="true" className="size-3 shrink-0 text-success-strong">
        ✓
      </span>
    );
  }
  return (
    <span aria-hidden="true" className="size-3 shrink-0 rounded-full border" />
  );
}
