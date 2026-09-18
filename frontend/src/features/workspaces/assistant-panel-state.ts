import type { WorkspaceTreeResponse } from './types'

/**
 * The panel's initial visibility comes from the persisted workspace state, not
 * from `localStorage`. `assistant_collapsed` mirrors `sidebar_collapsed`: a
 * `true` value means the panel is hidden, so "open" is its negation. The field
 * is absent until the Stage B backend lands, and an absent field means closed.
 *
 * Contract: roadmap T-D1 acceptance ("state survives reload via workspace
 * state") and docs/specs/nova-61-agentic-assistant-design.md (E5a keeps
 * conversation state in memory; a layout boolean is not conversation state).
 */
export function initialAssistantOpen(tree: Pick<WorkspaceTreeResponse, 'assistant_collapsed'>): boolean {
  return !(tree.assistant_collapsed ?? true)
}
