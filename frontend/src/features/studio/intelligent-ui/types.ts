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
/** What a chart, table or map node asks to save. */
export type UiArtifactDraft = {
  kind: "chart" | "table";
  title: string;
  chart?: { type: string; x: string; y: string; series: string[] };
};
/** A node saved by its source query; columns are the verified result's own. */
export type UiArtifactRequest = UiArtifactDraft & {
  evidenceId: string;
  columns: string[];
  /** Tool call of the query behind the result, when Nova recorded it. */
  toolCallId?: string;
  /** The result's own statement, when it was fetched by another participant. */
  sql?: string;
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
