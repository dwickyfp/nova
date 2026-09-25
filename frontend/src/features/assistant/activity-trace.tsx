import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { NoveMark } from "./nove-mark";
import type { TranscriptMessage } from "./use-assistant-transcript";

export type ActivityTraceProps = {
  message: TranscriptMessage;
};

export function ActivityTrace({ message }: ActivityTraceProps) {
  const steps = message.activity_steps ?? [];
  const plan = message.activity_plan ?? [];
  const running =
    steps.some((step) => step.status === "running") ||
    plan.some((step) => step.status !== "done");
  const [open, setOpen] = useState(false);

  if (steps.length === 0 && plan.length === 0) return null;

  return (
    <div
      data-role="activity"
      className="self-start text-xs text-muted-foreground"
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={
          running ? "Nove is working. Show activity" : "Show Nove activity"
        }
        className="inline-flex min-h-11 min-w-11 items-center gap-1.5 rounded-sm px-1 text-left hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:min-h-7 sm:min-w-0"
      >
        {running ? (
          <NoveMark className="size-4 animate-spin text-primary motion-reduce:animate-none" />
        ) : (
          <ChevronRight
            aria-hidden="true"
            className={cn("size-3.5 transition-transform", open && "rotate-90")}
          />
        )}
        {running ? (
          <span className="sr-only">Nove is working</span>
        ) : (
          <span>Activity</span>
        )}
      </button>

      {open ? (
        <div className="ml-2 mt-1 border-l border-border pl-3">
          {plan.length > 0 ? (
            <ol className="space-y-1">
              {plan.map((step) => (
                <li
                  key={step.id}
                  className={cn(step.status === "running" && "text-foreground")}
                >
                  {step.text}
                </li>
              ))}
            </ol>
          ) : (
            <ol className="space-y-1">
              {steps.map((step) => (
                <li
                  key={step.key}
                  className={cn(step.status === "running" && "text-foreground")}
                >
                  {step.text}
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : null}
    </div>
  );
}
