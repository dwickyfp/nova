import { api } from "@/lib/api-client";

export type Mode = "OBSERVE" | "GOVERNED" | "AUTONOMOUS";
export type Policy = {
  id: string;
  version: number;
  mode: Mode;
  collection_enabled: boolean;
  profile_sample_rate: number;
  regression_ratio: number;
  regression_absolute_ms: number;
  absolute_slow_ms: number;
  high_frequency: number;
  minimum_gain: number;
  observations_days: number;
  payload_hours: number;
  history_days: number;
  max_statistics_tables: number;
};
export type Scope = {
  principal: string;
  active_role: string | null;
  security_context_version: number;
  catalog: string;
  database: string;
  policy_revision: string | null;
  settings_hash: string;
};
export type Baseline = {
  eligible: boolean;
  historical_count: number;
  current_count: number;
  historical_p95: number | null;
  current_p95: number | null;
  upper_envelope: number | null;
  seasonal: boolean;
  reason: string | null;
};
export type RecordItem = {
  id: string;
  family_id?: string;
  cohort_id?: string;
  canonical?: string | null;
  classified?: boolean;
  scope?: Scope;
  tables?: string[];
  state?: string;
  reason?: string;
  kind?: string;
  detector?: string;
  version?: number;
  policy_version?: number;
  targets?: string[];
  evidence_ids?: string[];
  experiment_id?: string;
  approval_expires_at?: string;
  approved_by?: string;
  baseline?: Baseline;
  availability?: string;
  collected_at?: string;
  expires_at?: string;
  priority?: {
    score: number;
    contributions: Record<string, number>;
    estimated_gain: number;
    estimate_source: string;
    historical_outcomes: number;
  };
  diagnosis?: {
    category: string;
    confidence: number;
    explanation: string;
    evidence_ids: string[];
    counterevidence: string[];
  }[];
  next_steps?: string[];
  result?: {
    status: string;
    correctness: string;
    improvement: number | null;
    reason: string | null;
    before: Measurement;
    after: Measurement;
    repetitions: number;
    controls: { before: Measurement; after: Measurement };
  };
  plans?: { before?: Plan; after?: Plan };
  plan_diff?: {
    ordinal: number;
    before: Operator | null;
    after: Operator | null;
  }[];
  summary?: { operators?: Operator[]; row_count?: number; columns?: string[] };
  parameters?: Record<string, unknown>;
  measured_gain?: number | null;
  causality?: string;
};
export type Measurement = {
  count: number;
  mean_ms?: number;
  stddev_ms?: number;
  p95_ms?: number;
};
export type Operator = {
  ordinal: number;
  operator: string;
  estimates: Record<string, number>;
  attributes?: Record<string, string>;
};
export type Plan = { operators?: Operator[]; hash?: string };
export type Collection =
  | "families"
  | "incidents"
  | "opportunities"
  | "experiments"
  | "actions"
  | "jobs"
  | "evidence"
  | "outcomes"
  | "enrollments";
export type Page = { items: RecordItem[]; next_cursor: string | null };
export type Overview = {
  policy: Policy;
  counts: Record<string, number>;
  collection: {
    enabled: boolean;
    queued: number;
    capacity: number;
    dropped: number;
    persisted: number;
    failed_batches: number;
  };
  latency_basis: string;
};
const path = "/query-autopilot";
export const autopilot = {
  overview: (signal?: AbortSignal) =>
    api.get<Overview>(`${path}/overview`, signal),
  list: (collection: Collection, after: string, signal?: AbortSignal) =>
    api.get<Page>(
      `${path}/${collection}?${new URLSearchParams({ after, limit: "25" })}`,
      signal,
    ),
  detail: (collection: Collection, id: string, signal?: AbortSignal) =>
    api.get<RecordItem>(
      `${path}/${collection}/${encodeURIComponent(id)}`,
      signal,
    ),
  related: (
    collection: Collection,
    family: string,
    cohort: string,
    signal?: AbortSignal,
  ) =>
    api.get<Page>(
      `${path}/${collection}?${new URLSearchParams({ family_id: family, cohort_id: cohort, limit: "100" })}`,
      signal,
    ),
  mutate: (id: string, operation: string, version: number, key: string) =>
    api.post<RecordItem>(
      `${path}/opportunities/${encodeURIComponent(id)}/${operation}`,
      { candidate_version: version, idempotency_key: key },
    ),
  policy: (policy: Policy) => api.put<Policy>(`${path}/policies`, policy),
  enroll: (id: string, enrollment: unknown) =>
    api.put(`${path}/enrollments/${encodeURIComponent(id)}`, enrollment),
};
