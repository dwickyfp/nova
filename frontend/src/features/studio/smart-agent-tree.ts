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
