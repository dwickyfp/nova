/**
 * Chart artifacts in a Studio answer (protocol v2 `artifact.draft`,
 * `save.suggested`, `artifact.saved`).
 *
 * A draft is a native ChartDefinition bound to the answer's governed query by
 * its signed `draft_ref`. It carries the definition and nothing else: no rows,
 * no SQL. Saving is always the person's explicit action; the server contract
 * lives in `backend/app/modules/viz/studio_artifacts.py`.
 */
import type { ChartDefinition } from "@/features/native-dataviz/contracts";

export type ArtifactDraft = {
  draft_ref: string;
  kind: "dataviz_chart";
  title: string;
  chart: ChartDefinition;
  /** 1 for the chart the agent drew; each panel edit adds one. */
  revision: number;
  content_id?: string;
};

/** Where a draft was saved. */
export type ArtifactSaved = {
  operation_id: string;
  dashboard_id: string;
  dashboard_title: string;
  workspace_id: string;
  chart_id: string;
  widget_id: string;
  revision: number | null;
  draft_revision: number;
};

export type SaveCapability = "can_save" | "role_cannot_author";
