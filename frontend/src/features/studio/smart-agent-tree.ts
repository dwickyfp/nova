import type { AutoRun } from "@/features/agents/api";

const TERMINAL = new Set(["completed", "failed", "cancelled", "interrupted"]);

export function latestParticipants(runs: AutoRun[]): AutoRun[] {
  const histories = new Map<string, AutoRun[]>();
  for (const run of runs) {
    const id = run.agent_session_id ?? run.run_id;
    const history = histories.get(id) ?? [];
    history.push(run);
    histories.set(id, history);
  }
  return [...histories.values()].map((history) => {
    history.sort((a, b) => (a.turn_number ?? 1) - (b.turn_number ?? 1));
    return history.find((run) => !TERMINAL.has(run.status)) ?? history[history.length - 1];
  });
}

/** Tree reads after the root settles, after which a participant is shown as it is. */
export const SETTLE_READS = 20;

/**
 * How soon to read the run tree again. The root can settle a moment before a
 * participant's last status is readable, so a few more reads follow; without
 * them its card stays on "Queued".
 */
export function treeRefetchInterval(
  runs: AutoRun[],
  streaming: boolean,
  readsSinceSettled: number,
): number | false {
  const root = runs.find((run) => run.depth === 0)?.status;
  if (streaming || (root && !TERMINAL.has(root))) return 1000;
  const unsettled = runs.some((run) => run.depth > 0 && !TERMINAL.has(run.status));
  return unsettled && readsSinceSettled <= SETTLE_READS ? 3000 : false;
}
