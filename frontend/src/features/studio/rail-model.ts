import type { RailStep } from "./thought-turn";

/** The row label for a provider's reasoning summary. */
export const MODEL_REASONING_LABEL = "Model reasoning";

export function isLiveStatus(status: RailStep["status"]): boolean {
  return status === "running" || status === "pending";
}

export function isFailedStatus(status: RailStep["status"]): boolean {
  return status === "failed" || status === "denied";
}

/** A step the user stopped is neither a failure nor a success. */
export function isStoppedStatus(status: RailStep["status"]): boolean {
  return status === "cancelled";
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

/** How much of a reasoning summary the one-line preview shows. */
const PREVIEW_CHARS = 140;

/**
 * One line of a reasoning summary for its row: the newest words while it
 * streams, its first line (often a bold title) once it is done.
 */
export function reasoningPreview(body: string, live: boolean): string {
  const plain = body.replace(/[*_`#>]+/g, "").replace(/\s+/g, " ").trim();
  if (!plain) return "";
  if (live) return plain.length > PREVIEW_CHARS ? `…${plain.slice(-PREVIEW_CHARS)}` : plain;
  const first = body.split("\n").map((line) => line.replace(/[*_`#>]+/g, "").trim()).find(Boolean) ?? "";
  return first.length > PREVIEW_CHARS ? `${first.slice(0, PREVIEW_CHARS - 1)}…` : first;
}
