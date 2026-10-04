import { api } from "@/lib/api-client";
import type { SemanticRef } from "./lifecycle-api";

export type LearningSource = {
  kind: "semantic_usage" | "verified_query" | "mission_deliverable" | "decision" | "outcome" | "context_query_pattern";
  id: string;
  revision?: number;
  mission_id?: string;
};

export type LearningRequest = {
  agent_id: string;
  thread_id: string;
  semantic: SemanticRef;
  sources: LearningSource[];
};

export type LearningObservation = {
  source_kind: string;
  source_identity: string;
  source_revision: number | null;
  authority: "usage_observation" | "published_verified_query" | "lifecycle_evidence";
  semantic: SemanticRef;
  state: "INFERRED";
  validity: "current" | "historical";
  freshness: "unknown";
  shape: {
    metrics: string[];
    dimensions: string[];
    filter_shape: { field: string; operator: string }[];
    time_grain: string | null;
  };
  evidence_refs: string[];
  observations: number | null;
  attribution: "observed_after" | "association" | "supported_effect" | "unknown";
};

export type LearningSummary = {
  method: "governed-learning-sources-v1";
  semantic: SemanticRef;
  digest: string;
  observations: LearningObservation[];
  review_required: true;
  bounded: true;
  reason?: "learning_disabled";
};

export function inspectLearningSources(request: LearningRequest) {
  return api.post<LearningSummary>(
    `/semantic-views/${encodeURIComponent(request.semantic.view_id)}/autopilot/observations`,
    request,
  );
}
