import type { EvidenceRows } from "./catalog";
import type { UiValidation } from "./validator";

/**
 * A native Dataviz chart for one `Chart` node of the program, bound to the
 * governed query of the result it draws. `node` names the node by its
 * arguments; a null argument matches any value.
 */
export type UiNativeChart = {
  node: { x?: unknown; y?: unknown; type?: unknown; title?: unknown };
  title: string;
  chart: Record<string, unknown>;
  draft_ref?: string;
};

export type UiBlock = {
  catalog_version: string;
  program_sha256: string;
  program: string;
  fallback: string;
  evidence: EvidenceRows;
  state_defaults: Record<string, unknown>;
  actions: UiValidation["actions"];
  provenance: {
    evidence_ids: string[];
    scope: string;
    assumption: boolean;
    omitted_rows?: number;
    /** "deterministic" blocks are drawn from verified results without a model call. */
    composer?: "model" | "deterministic";
    /** The query tool call behind each bound result, for saving it as an artifact. */
    tool_calls?: Record<string, string>;
    /** The statement behind a result another participant fetched (no tool call here). */
    statements?: Record<string, string>;
  };
  /** Native charts for the program's evidence-bound `Chart` nodes, in order. */
  charts?: UiNativeChart[];
};
/** Where a turn's interactive answer is stored, for Dataviz references. */
export type UiDatavizSource = {
  threadId: string;
  messageId: string;
  contentId: string;
  /** The block is a whole chart (a stored chart drawn by the UI), not a node. */
  whole?: boolean;
};
export type UiSource = {
  /** The Studio agent; absent for a Neva conversation. */
  agentId?: string | null;
  threadId: string;
  messageId: string;
  contentId: string;
};
export type UiAction = {
  message_id: string;
  content_id: string;
  program_sha256: string;
  action_id: string;
  operation_id: string;
  state: Record<string, unknown>;
};
export type UiState = {
  revision: number;
  state: Record<string, unknown>;
  program_sha256: string;
};
