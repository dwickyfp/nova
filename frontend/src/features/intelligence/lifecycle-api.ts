import { api } from "@/lib/api-client";

export type SemanticRef = {
  view_id: string;
  version: number;
  fingerprint: string;
};
export type Evidence = {
  id: string;
  source_type: string;
  source_id: string;
  method: string;
  digest: string;
  observed_at: string;
  semantic?: SemanticRef;
  window_start?: string;
  window_end?: string;
};
export type LifecycleRecord = {
  id: string;
  revision: number;
  created_at: string;
  title?: string;
  status: string;
  semantic: SemanticRef;
  evidence: Evidence[];
};
export type News = LifecycleRecord & {
  title: string;
  summary: string;
  before: number;
  after: number;
  change: number;
  severity: string;
  confidence: { label: string; dimension: string };
  investigation_id?: string;
  monitor_id: string;
  window: { start: string; end: string };
};
export type Investigation = LifecycleRecord & {
  news_id: string;
  hypotheses: {
    id: string;
    label: string;
    contribution?: number;
    dimension?: string;
    causal_status: string;
    next_test: string;
    evidence_ids: string[];
  }[];
  residual: number;
  decompositions: {
    dimension: string;
    change: string;
    residual: string;
    reconciled: boolean;
  }[];
  timeline: {
    at: string;
    kind: string;
    label?: string;
    before?: number;
    after?: number;
    causal_status?: string;
  }[];
};
export type Decision = LifecycleRecord & {
  title: string;
  currency: string;
  options: {
    id: string;
    description: string;
    action_type: string;
    assumptions: Record<string, string | boolean | number>;
    prediction: number;
    lower_bound?: number;
    upper_bound?: number;
    cost: number;
    incremental_gross_profit: number;
    risk: string;
    feasible: boolean;
    method: string;
    run_id?: string;
  }[];
  selected_option_id?: string;
  policy?: { decision: string; reason: string; policy_revision: number };
  outcome_window: { start: string; end: string };
};
export type Outcome = LifecycleRecord & {
  actual?: number;
  predicted: number;
  completeness: number;
  attribution: string;
  dimensions: Record<string, string | boolean | number | null>;
};
export type Page<T> = { items: T[]; next_after: string | null };
export const intelligenceApi = {
  policy: (id: string) =>
    api.get<{
      current: boolean;
      policy_revision: number;
      can_review: boolean;
      can_edit: boolean;
    }>(`/intelligence/decisions/${encodeURIComponent(id)}/policy`),
  page: <T>(kind: string, after = "") =>
    api.get<Page<T>>(
      `/intelligence/${kind}?after=${encodeURIComponent(after)}`,
    ),
  get: <T>(kind: string, id: string) =>
    api.get<T>(`/intelligence/${kind}/${encodeURIComponent(id)}`),
  investigate: (id: string) =>
    api.post<Investigation>(
      `/intelligence/news/${encodeURIComponent(id)}/investigate`,
    ),
  operate: (decision: Decision, operation: string, option_id?: string) =>
    api.post<Decision>(
      `/intelligence/decisions/${encodeURIComponent(decision.id)}/operations`,
      {
        operation,
        option_id,
        expected_revision: decision.revision,
        operation_id: crypto.randomUUID(),
      },
    ),
  outcome: (id: string) =>
    api.post<Outcome>(
      `/intelligence/decisions/${encodeURIComponent(id)}/evaluate-outcome`,
    ),
  lineage: (id: string) =>
    api.get<{
      news: News;
      investigation: Investigation;
      decision: Decision;
      evidence: Evidence[];
      events?: {
        id: string;
        event: string;
        decision_revision: number;
        actor: string;
      }[];
      outcomes?: Outcome[];
    }>(`/intelligence/decisions/${encodeURIComponent(id)}/lineage`),
};
