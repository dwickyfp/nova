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
  provenance: { evidence_ids: string[]; scope: string; assumption: boolean; omitted_rows?: number };
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
