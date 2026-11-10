import { ProcessRail, type RailStep } from "@/features/studio/thought-turn";
import type { TranscriptMessage } from "./use-assistant-transcript";

export type ActivityTraceProps = {
  message: TranscriptMessage;
};

/** Neva's work for one answer, shown on the same process rail as Studio. */
export function railSteps(message: TranscriptMessage): RailStep[] {
  const steps = message.activity_steps ?? [];
  if (steps.length) {
    return steps.map((step) => ({
      id: step.key,
      kind: "thinking",
      label: step.phase,
      text: step.text,
      status: step.status,
      ...(step.detail ? { detail: step.detail } : {}),
    }));
  }
  // Before the first step arrives the plan is all there is to show.
  return (message.activity_plan ?? []).map((step) => ({
    id: step.id,
    kind: "note",
    label: "plan",
    text: step.text,
    status: step.status === "done" ? "done" : "running",
  }));
}

export function ActivityTrace({ message }: ActivityTraceProps) {
  const steps = railSteps(message);
  if (!steps.length) return null;
  const running =
    (message.activity_steps ?? []).some((step) => step.status === "running") ||
    (message.activity_plan ?? []).some((step) => step.status !== "done");
  return (
    <div data-role="activity" className="min-w-0 self-stretch text-xs">
      <ProcessRail steps={steps} running={running} />
    </div>
  );
}
