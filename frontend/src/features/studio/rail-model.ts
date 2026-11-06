import type { RailStep } from "./thought-turn";

/**
 * The shape the rail draws: plain steps, and one branch per specialist Smart
 * called. A branch gathers that specialist's own rows under the row that
 * called it, so a run with three specialists reads as three tasks instead of a
 * dozen interleaved lines.
 */
export type BranchStatus = "running" | "done" | "failed" | "stopped";

export type RailItem =
  | { type: "step"; id: string; step: RailStep }
  | {
      type: "branch";
      id: string;
      head: RailStep;
      steps: RailStep[];
      end?: RailStep;
      status: BranchStatus;
    };

/** Lifecycle rows a branch shows through its mark, not as rows of their own. */
const LIFECYCLE = new Set(["started", "completed", "agent_failed", "agent_cancelled", "agent_interrupted"]);

export function isLiveStatus(status: RailStep["status"]): boolean {
  return status === "running" || status === "pending";
}

export function isFailedStatus(status: RailStep["status"]): boolean {
  return status === "failed" || status === "denied" || status === "cancelled";
}

export function groupRailSteps(steps: RailStep[], running: boolean): RailItem[] {
  const items: RailItem[] = [];
  const branches = new Map<string, Extract<RailItem, { type: "branch" }>>();

  for (const step of steps) {
    const child = step.childRunId;
    if (child && step.kind === "delegate" && step.label === "queued" && !branches.has(child)) {
      const branch = { type: "branch" as const, id: step.id, head: step, steps: [], status: "running" as BranchStatus };
      branches.set(child, branch);
      items.push(branch);
      continue;
    }
    const branch = child ? branches.get(child) : undefined;
    if (!branch) {
      items.push({ type: "step", id: step.id, step });
      continue;
    }
    if (LIFECYCLE.has(step.label)) {
      if (step.label !== "started") branch.end = step;
      continue;
    }
    branch.steps.push(step);
  }

  for (const branch of branches.values()) {
    const failed = (branch.end && isFailedStatus(branch.end.status)) || branch.steps.some((step) => isFailedStatus(step.status));
    branch.status = failed
      ? "failed"
      : branch.end?.label === "completed"
        ? "done"
        : running
          ? "running"
          : "stopped";
  }
  return items;
}

/** Remove the "Specialist: " prefix a row carries when it already sits under that specialist. */
export function branchStepText(step: RailStep): string {
  const prefix = step.actor ? `${step.actor}: ` : "";
  return prefix && step.text.startsWith(prefix) ? step.text.slice(prefix.length) : step.text;
}

/** Milliseconds between two recorded event times, when both parse. */
export function recordedSpan(from?: string, to?: string): number | undefined {
  if (!from || !to) return undefined;
  const span = Date.parse(to) - Date.parse(from);
  return Number.isFinite(span) && span >= 0 ? span : undefined;
}

/** `4.7s` under a minute, `1m 05s` above it. */
export function formatSpan(ms: number): string {
  const seconds = Math.max(0, ms) / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, "0")}s`;
}

/** One line from a longer result, for the branch's closing row. */
export function resultLine(text: string | undefined): string | undefined {
  const line = text?.trim().split(/\n+/).find((part) => part.trim())?.replace(/^[#>*\-\s]+/, "").trim();
  if (!line) return undefined;
  return line.length > 120 ? `${line.slice(0, 117).trimEnd()}…` : line;
}
