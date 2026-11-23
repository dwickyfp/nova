import { api } from "@/lib/api-client";
import type { ChartDefinition, DatasetField } from "@/features/native-dataviz/contracts";
import type { DatavizTarget } from "../../dataviz/api";
import type { ArtifactDraft, ArtifactSaved, SaveCapability } from "./types";

const BASE = "/agents/studio/dataviz";
const path = (ref: string) => `${BASE}/drafts/${encodeURIComponent(ref)}`;

/** A server event (`artifact.draft` or `artifact.saved`) as stored on the answer. */
export type ArtifactEventRecord = { event: string; data: Record<string, unknown> };

export type DraftState = {
  capability: SaveCapability;
  reason_code: string;
  message: string;
  draft: ArtifactDraft | null;
  revision: number;
  saved: ArtifactSaved | null;
  /** The query's columns as your role may see them; never rows. */
  fields: DatasetField[];
};

export type DraftPublication = ArtifactSaved & { status: string; event: ArtifactEventRecord | null };

export const artifactsApi = {
  state: (ref: string, signal?: AbortSignal) => api.get<DraftState>(path(ref), signal),
  revise: (ref: string, chart: ChartDefinition, baseRevision: number) =>
    api.post<{ revision: number; event: ArtifactEventRecord }>(`${path(ref)}/revisions`, {
      chart,
      base_revision: baseRevision,
    }),
  publish: (body: { draft_ref: string; idempotency_key: string; target: DatavizTarget; title?: string }) =>
    api.post<DraftPublication>(`${BASE}/publish`, body),
};
