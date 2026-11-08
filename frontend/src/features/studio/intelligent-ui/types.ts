import type { EvidenceRows } from "./catalog";
import type { UiValidation } from "./validator";

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
  agentId: string;
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
