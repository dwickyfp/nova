import { SKILL_AUTHOR_ID } from "./skill-document";

/** Per-browser key for the agent the user last chose or chatted with. */
export const LAST_AGENT_KEY = "nova.studio.lastAgent";

export function readLastAgent(): string | null {
  try {
    return window.localStorage.getItem(LAST_AGENT_KEY);
  } catch {
    return null;
  }
}

export function rememberLastAgent(agentId: string): void {
  // The skill creator is a one-off authoring chat, not a default to come back to.
  if (!agentId || agentId === SKILL_AUTHOR_ID) return;
  try {
    window.localStorage.setItem(LAST_AGENT_KEY, agentId);
  } catch {
    // Storage can be blocked; Studio then falls back to the first agent.
  }
}

/**
 * The agent Studio opens without an explicit choice: the last one used in this
 * browser while it is still in the catalog, otherwise the first agent.
 */
export function pickDefaultAgent(
  agents: readonly { agent_id: string }[],
  lastAgentId: string | null = readLastAgent(),
): string | null {
  if (lastAgentId && agents.some((agent) => agent.agent_id === lastAgentId)) return lastAgentId;
  return agents[0]?.agent_id ?? null;
}
