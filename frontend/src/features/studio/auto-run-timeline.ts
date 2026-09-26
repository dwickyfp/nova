import { agentsApi, type Agent, type AutoRunEvent, type AutoThreadRun } from "@/features/agents/api";
import type { RailStep } from "./thought-turn";

const TERMINAL = new Set(["agent_completed", "agent_failed", "agent_cancelled", "agent_interrupted"]);

function string(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function short(text: string): string {
  return text.length > 130 ? `${text.slice(0, 127).trimEnd()}…` : text;
}

export function rootRunForTurn(
  turn: { id: string; question: string; messageId?: string; state: string },
  chronologicalRuns: AutoThreadRun[],
  chronologicalTurns: { id: string; question: string }[],
  activeRunId: string | null,
): string | null {
  const exact = chronologicalRuns.find((run) =>
    (turn.messageId && run.final_message_id === turn.messageId) ||
    (run.user_message_id && run.user_message_id === turn.id),
  );
  if (exact) return exact.run_id;
  if (activeRunId && chronologicalTurns[chronologicalTurns.length - 1]?.id === turn.id &&
      (turn.state === "streaming" || turn.id.startsWith("turn-"))) {
    return activeRunId;
  }
  const position = chronologicalTurns.findIndex((candidate) => candidate.id === turn.id);
  const ordered = chronologicalRuns[position];
  return ordered?.objective === turn.question ? ordered.run_id : null;
}

export async function loadAutoRunEvents(rootRunId: string, previous: AutoRunEvent[] = []): Promise<AutoRunEvent[]> {
  const events: AutoRunEvent[] = [...previous];
  let after = events.length ? events[events.length - 1].event_id : -1;
  for (let page = 0; page < 100; page += 1) {
    const response = await agentsApi.getAutoRunEvents(rootRunId, after);
    if (!response.events.length) break;
    events.push(...response.events);
    const next = response.events[response.events.length - 1].event_id;
    if (next <= after) break;
    after = next;
    if (response.events.length < 100) break;
  }
  return [...new Map(events.map((event) => [event.event_id, event])).values()];
}

export function autoRunSteps(
  events: AutoRunEvent[],
  rootRunId: string,
  agents: Agent[],
): RailStep[] {
  const ordered = [...events].sort((a, b) => a.event_id - b.event_id);
  const childNames = new Map<string, string>();
  for (const event of ordered) {
    if (event.type !== "agent_queued" || event.run_id === rootRunId) continue;
    const agentId = string(event.payload.agent_id);
    childNames.set(
      event.run_id,
      string(event.payload.agent_name) ||
      agents.find((agent) => agent.agent_id === agentId)?.name || "Specialist",
    );
  }
  const settledAfter = (event: AutoRunEvent, types: string[]) => ordered.some((later) =>
    later.run_id === event.run_id && later.event_id > event.event_id && types.includes(later.type),
  );
  const nameOf = (runId: string) => runId === rootRunId ? "Main" : childNames.get(runId) ?? "Specialist";
  const rows: RailStep[] = [];

  for (const event of ordered) {
    const childRunId = event.run_id === rootRunId ? undefined : event.run_id;
    const name = nameOf(event.run_id);
    const payload = event.payload;
    const base = {
      id: `auto-${event.event_id}`,
      childRunId,
      status: "done" as const,
    };
    switch (event.type) {
      case "agent_queued":
        rows.push({ ...base, kind: childRunId ? "delegate" : "note", label: "queued", text: childRunId ? `Called ${name}` : "Main queued", body: childRunId ? string(payload.objective) : undefined });
        break;
      case "agent_started":
        rows.push({ ...base, kind: childRunId ? "delegate" : "note", label: "started", text: childRunId ? `${name} started working` : "Main started working", status: settledAfter(event, ["agent_waiting", "agent_started", ...TERMINAL]) ? "done" : "running" });
        break;
      case "delegation_plan": {
        const summary = string(payload.summary);
        if (summary) rows.push({ ...base, kind: "note", label: "plan", text: short(summary), body: summary });
        break;
      }
      case "agent_waiting":
        rows.push({ ...base, kind: "note", label: "waiting", text: `${name} waiting for specialists`, status: settledAfter(event, ["agent_started", ...TERMINAL]) ? "done" : "running" });
        break;
      case "agent_resumed":
        rows.push({ ...base, kind: "note", label: "resumed", text: "Main resumed after specialist updates" });
        break;
      case "agent_message": {
        const sender = string(payload.sender_run_id);
        const recipient = string(payload.recipient_run_id);
        const author = payload.origin === "user" ? "You" : nameOf(sender);
        const destination = nameOf(recipient);
        const teammate = sender === rootRunId ? recipient : sender;
        const delegated = childNames.has(teammate);
        rows.push({ ...base, kind: delegated ? "delegate" : "note", label: "message", text: `${author} messaged ${destination}`, body: string(payload.content), childRunId: delegated ? teammate : undefined });
        break;
      }
      case "tool_activity": {
        const tool = string(payload.tool_name);
        const hasDetailedEvent = ordered.some((item) =>
          item.run_id === event.run_id && item.type === "child_activity" &&
          item.payload.event_type === "tool_call" && item.payload.tool_name === tool,
        );
        if (tool && !hasDetailedEvent) rows.push({ ...base, kind: "tool", label: tool, text: `${name} called ${tool}` });
        break;
      }
      case "child_activity": {
        const kind = string(payload.event_type);
        if (kind === "tool_call") {
          const tool = string(payload.tool_name);
          rows.push({ ...base, kind: "tool", label: tool || "tool", text: `${name} called ${tool || "a tool"}`, preview: string(payload.sql_preview) || undefined });
        } else if (kind === "tool_progress" || kind === "thinking" || kind === "plan") {
          const detail = string(payload.text);
          if (detail) rows.push({ ...base, kind: childRunId ? "delegate" : "note", label: kind, text: `${name}: ${short(detail)}`, body: detail });
        } else if (kind === "error") {
          const detail = string(payload.message);
          if (detail) rows.push({ ...base, kind: "note", label: "error", text: `${name}: ${short(detail)}`, body: detail, status: "failed" });
        }
        break;
      }
      case "agent_completed":
        rows.push({ ...base, kind: childRunId ? "delegate" : "note", label: "completed", text: childRunId ? `${name} finished` : "Main finished the answer" });
        break;
      case "agent_failed":
      case "agent_cancelled":
      case "agent_interrupted":
        rows.push({ ...base, kind: childRunId ? "delegate" : "note", label: event.type, text: `${name} ${event.type.slice(6).replace("_", " ")}`, status: "failed" });
        break;
    }
  }
  return rows;
}
