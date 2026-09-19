/**
 * The persisted field is `assistant_collapsed` (true = hidden), while the panel
 * state is `assistantOpen` (true = visible). These two helpers are the only
 * place the polarity is translated, so a write and a read cannot drift apart.
 *
 * `assistant_collapsed` mirrors `sidebar_collapsed` and arrives on the
 * workspaces tree; the field is optional because it was absent before the Stage
 * B backend landed. Contract: roadmap T-D1 acceptance ("state survives reload
 * via workspace state") and
 * docs/specs/nova-61-agentic-assistant-design.md (E5a keeps conversation state
 * in memory; a layout boolean is not conversation state).
 */
export function initialAssistantOpen(tree: { assistant_collapsed?: boolean }): boolean {
  return !(tree.assistant_collapsed ?? true)
}

export function assistantCollapsedToPersist(open: boolean): boolean {
  return !open
}
