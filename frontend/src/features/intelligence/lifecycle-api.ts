import { api } from "@/lib/api-client";
import type { ScenarioDefinition, ScenarioValue } from "./scenario-schema";

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
  agent_id?: string;
  thread_id?: string | null;
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
  action_ids?: string[];
  actual?: number;
  predicted: number;
  completeness: number;
  attribution: string;
  dimensions: Record<string, string | boolean | number | null>;
};
export type Page<T> = { items: T[]; next_after: string | null };

export type MonitorConfiguration = {
  name: string;
  agent_id: string;
  semantic: SemanticRef;
  plan: Record<string, unknown>;
  value_column: string;
  count_column: string;
  time_dimension: string;
  completeness_column?: string | null;
  driver_dimensions?: string[];
  related_monitor_ids?: string[];
  relative_threshold?: number;
  absolute_threshold?: number;
  minimum_samples?: number;
  baseline_weeks?: number;
  window_hours?: number;
  cooldown_hours?: number;
  cadence_minutes?: number;
  timezone?: string;
  enabled?: boolean;
};
export type BusinessAction = {
  id: string;
  revision: number;
  decision_id: string;
  decision_revision: number;
  option_id: string;
  adapter_id: string;
  configuration: MonitorConfiguration;
  status:
    | "awaiting_approval"
    | "approved"
    | "awaiting_consent"
    | "dispatch_ready"
    | "executing"
    | "verification_required"
    | "verified"
    | "failed"
    | "compensating"
    | "compensation_required"
    | "compensated"
    | "cancelled"
    | "denied";
  expected_effect: string;
  request_digest: string;
  dispatch_attempts: number;
  compensation_attempts?: number;
  policy: {
    decision: "ALLOW" | "DENY" | "REQUIRE_APPROVAL";
    reason: string;
    policy_revision: number;
  };
  approval?: { actor: string; active_role: string; approved_at: string } | null;
  consent_call_id?: string | null;
  receipt?: {
    monitor_id: string;
    monitor_revision: number;
    task_id?: string | null;
    schedule_enabled: boolean;
  } | null;
  compensation_receipt?: BusinessAction["receipt"];
  verification?: {
    checked_at: string;
    complete: boolean;
    reason: string;
  } | null;
  compensation?: {
    checked_at: string;
    complete: boolean;
    reason: string;
  } | null;
  error_code?: string | null;
};
export type ActionPreviewInput = {
  idempotency_key: string;
  decision_id: string;
  expected_decision_revision: number;
  option_id: string;
  adapter_id: "monitor-v1";
  configuration: MonitorConfiguration;
};
export type ActionOperation = {
  operation_id: string;
  expected_revision: number;
  thread_id: string;
};
export const actionApi = {
  get: (id: string, signal?: AbortSignal) =>
    api.get<BusinessAction>(
      `/intelligence/actions/${encodeURIComponent(id)}`,
      signal,
    ),
  preview: (body: ActionPreviewInput) =>
    api.post<BusinessAction>("/intelligence/actions/preview", body),
  review: (
    action: BusinessAction,
    operation: "approve" | "deny",
    operation_id: string,
  ) =>
    api.post<{
      id: string;
      revision: number;
      status: BusinessAction["status"];
    }>(`/intelligence/actions/${encodeURIComponent(action.id)}/review`, {
      operation,
      operation_id,
      expected_revision: action.revision,
    }),
  operate: (
    id: string,
    operation: "execute" | "verify" | "compensate" | "cancel",
    body: ActionOperation,
  ) =>
    api.post<BusinessAction>(
      `/intelligence/actions/${encodeURIComponent(id)}/${operation}`,
      body,
    ),
};
export const intelligenceApi = {
  scenarios: (signal?: AbortSignal) =>
    api.get<{ items: ScenarioDefinition[] }>("/intelligence/scenarios", signal),
  simulateScenario: (
    definition: ScenarioDefinition,
    parameters: Record<string, ScenarioValue>,
    operation_id: string,
  ) =>
    api.post<Record<string, unknown>>("/intelligence/decision-lab/scenarios", {
      scenario_kind: definition.scenario_kind,
      scenario_version: definition.version,
      parameters,
      operation_id,
    }),
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
