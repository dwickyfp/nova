import type { RailStep } from "./thought-turn";

export function isLiveStatus(status: RailStep["status"]): boolean {
  return status === "running" || status === "pending";
}

export function isFailedStatus(status: RailStep["status"]): boolean {
  return status === "failed" || status === "denied" || status === "cancelled";
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
