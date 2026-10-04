import { api } from "@/lib/api-client";

export const qualityScorers = [
  "semantic_selection",
  "tool_selection",
  "tool_arguments",
  "numeric_consistency",
  "evidence_coverage",
  "clarification_quality",
  "task_completeness",
  "policy_compliance",
  "action_verification",
  "latency",
  "efficiency",
] as const;

export const qualityBehavioralScorers = qualityScorers.filter(
  (scorer) => scorer !== "latency" && scorer !== "efficiency",
);
export const qualityCountBudgets = [
  ["tool_calls", "Tool calls"],
  ["provider_calls", "Provider calls"],
  ["total_tokens", "Total tokens"],
  ["context_tokens", "Context tokens"],
  ["participants", "Participants"],
  ["metadata_reads", "Metadata reads"],
] as const;
export type QualityCount = (typeof qualityCountBudgets)[number][0];
export type PromotionGates = {
  other_cases: "report_only" | "mandatory" | "all";
  required_scorers: (typeof qualityBehavioralScorers)[number][];
  performance: "report_only" | "required";
  max_latency_ms: number | null;
  count_budgets: Partial<Record<QualityCount, number>>;
};
export type QualityGateStatus =
  "passed" | "failed" | "unavailable" | "not_configured";
type QualityGateResult = {
  status: QualityGateStatus;
  required: boolean;
};
export type QualityGateResults = {
  mandatory_critical?: QualityGateResult & { case_ids: string[] };
  other_quality?: QualityGateResult & {
    case_ids: string[];
    required_scorers: string[];
    mode: PromotionGates["other_cases"];
  };
  performance?: QualityGateResult & {
    mode: PromotionGates["performance"];
    max_latency_ms: number | null;
    count_budgets: Partial<Record<QualityCount, number>>;
  };
};

export type QualityAssertion = {
  scorer: string;
  required: boolean;
  expected: unknown;
};
export type QualityCase = {
  id: string;
  revision: number;
  name: string;
  prompt: string;
  mandatory: boolean;
  critical: boolean;
  assertions: QualityAssertion[];
  source?:
    | "manual"
    | "regression"
    | "production_failure"
    | "verified_query"
    | "feedback";
};
export type QualityCaseInput = Omit<QualityCase, "id" | "revision">;
export type QualityScore = {
  scorer: string;
  status: "pass" | "fail" | "unavailable";
  detail: string;
  required: boolean;
  failure_taxonomy?: string;
  version?: string;
  scorer_version?: string;
};
export type QualityResult = {
  case_id: string;
  case_revision: number;
  status: string;
  scores: QualityScore[];
  budget_scores?: QualityScore[];
  trace_id?: string | null;
  duration_ms?: number | null;
  tool_calls?: number | null;
  total_tokens?: number | null;
  trace?: {
    counts?: Partial<Record<QualityCount, number>>;
    duration_ms?: number;
  };
};
export type QualityRun = {
  cases?: QualityCase[];
  id: string;
  revision: number;
  agent_id: string;
  version_id: string;
  manifest_id: string;
  scorer_set_version?: string;
  status: "running" | "passed" | "failed" | "unavailable";
  promotion_eligible: boolean;
  gates?: PromotionGates;
  gate_results?: QualityGateResults | null;
  results: QualityResult[];
  created_at: string;
};
export type ReleaseManifest = {
  id?: string;
  manifest_id?: string;
  fingerprint?: string;
  version_id?: string;
  scorer_set_version?: string;
  dependencies?: Record<string, unknown> | unknown[];
  external_mutability?: string[];
  limitations?: string[];
  [key: string]: unknown;
};
export type QualityMonitoring = {
  revision?: number;
  cadence_minutes?: number;
  enabled: boolean;
  sample_rate: number;
  max_traces: number;
};
export type QualityProposal = {
  id: string;
  revision: number;
  status: string;
  title?: string;
  diagnosis?: string | string[];
  failure_taxonomy?: string[];
  run_id?: string;
  hypothesis?: boolean;
  suggestion?: string;
  changes?: { target: string; description: string }[];
  doctor?: DoctorReport;
  patches?: RemediationPatch[];
  regression_candidates?: RegressionCaseCandidate[];
  review_inputs?: {
    revision: number;
    resolution: "accepted" | "rejected";
    patch_id: string | null;
    regression_case_ids: string[];
  };
  application?: {
    operation_id: string;
    status: "pending" | "applied";
    kind: "agent_draft" | "semantic_proposal";
    base_revision: string;
    patch_id: string;
    version_id?: string;
    proposal_id?: string;
    view_id?: string;
    regression_cases?: { id: string; revision: number }[];
  };
};

export type RemediationPatch = {
  id: string;
  kind: "restore_configuration" | "append_instruction" | "semantic_changes";
  description: string;
  hypothesis: boolean;
  base_revision: string;
  source_version_id?: string;
  fields?: string[];
  instruction?: string;
  semantic?: { view_id: string; version: number; fingerprint: string };
  changes?: { kind: string; name: string; synonyms?: string[] }[];
};
export type RegressionCaseCandidate = {
  id: string;
  run_id: string;
  case_id: string;
  case_revision: number;
  scorers: string[];
  review_required: boolean;
};
type EvaluatedReleaseRef = {
  id: string;
  version_id: string;
  manifest_id: string;
  manifest_fingerprint: string;
  created_at: string;
};
export type DoctorReport = {
  schema_version: number;
  known_good: EvaluatedReleaseRef | null;
  first_bad: EvaluatedReleaseRef | null;
  current: EvaluatedReleaseRef | null;
  regressions: {
    case_id: string;
    case_revision: number;
    scorer: string;
    scorer_version: string;
    before: string;
    after: string;
    before_trace_id?: string | null;
    after_trace_id?: string | null;
    measurements: {
      name: string;
      before: number;
      after: number;
      delta: number;
    }[];
    hypothesis: boolean;
  }[];
  changed_dependencies: {
    category: string;
    fields?: string[];
    hypothesis: boolean;
  }[];
  findings: {
    category: string;
    detail: string;
    hypothesis: boolean;
    semantic?: { view_id: string; version: number; fingerprint: string };
    collisions?: { alias: string; metrics: string[] }[];
    changes?: { metric: string; fields: string[] }[];
  }[];
  requirements: { code: string; detail: string }[];
  patches: RemediationPatch[];
  regression_candidates: RegressionCaseCandidate[];
};

const base = (id: string) => `/agents/${encodeURIComponent(id)}`;
export const qualityApi = {
  cases: (id: string, signal?: AbortSignal) =>
    api.get<{ items: QualityCase[] }>(`${base(id)}/quality/cases`, signal),
  createCase: (id: string, body: QualityCaseInput) =>
    api.post<QualityCase>(`${base(id)}/quality/cases`, body),
  updateCase: (id: string, item: QualityCase, body: QualityCaseInput) =>
    api.put<QualityCase>(
      `${base(id)}/quality/cases/${encodeURIComponent(item.id)}`,
      { ...body, expected_revision: item.revision },
    ),
  runs: (id: string, signal?: AbortSignal) =>
    api.get<{ items: QualityRun[] }>(`${base(id)}/quality/runs`, signal),
  run: (id: string, run: string, signal?: AbortSignal) =>
    api.get<QualityRun>(
      `${base(id)}/quality/runs/${encodeURIComponent(run)}`,
      signal,
    ),
  evaluate: (
    id: string,
    version_id: string,
    case_ids?: string[],
    gates?: PromotionGates,
  ) =>
    api.post<QualityRun>(`${base(id)}/quality/runs`, {
      version_id,
      ...(case_ids ? { case_ids } : {}),
      ...(gates ? { gates } : {}),
    }),
  comparison: (id: string, left: string, right: string, signal?: AbortSignal) =>
    api.get<{ left: QualityRun; right: QualityRun; changes: unknown }>(
      `${base(id)}/quality/comparison?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`,
      signal,
    ),
  manifest: (id: string, version: string, signal?: AbortSignal) =>
    api.get<{ manifest: ReleaseManifest | null; status: string }>(
      `${base(id)}/versions/${encodeURIComponent(version)}/manifest`,
      signal,
    ),
  pinManifest: (id: string, version: string) =>
    api.post<ReleaseManifest>(
      `${base(id)}/versions/${encodeURIComponent(version)}/manifest`,
    ),
  publish: (
    id: string,
    version: string,
    expected_revision: string | null,
    quality_run_id: string,
  ) =>
    api.post(`${base(id)}/versions/${encodeURIComponent(version)}/publish`, {
      expected_revision,
      quality_run_id,
    }),
  monitoring: (id: string, signal?: AbortSignal) =>
    api.get<QualityMonitoring>(`${base(id)}/quality/monitoring`, signal),
  saveMonitoring: (
    id: string,
    body: QualityMonitoring & {
      expected_revision?: number;
    },
  ) => api.put<QualityMonitoring>(`${base(id)}/quality/monitoring`, body),
  proposals: (id: string, signal?: AbortSignal) =>
    api.get<{ items: QualityProposal[] }>(
      `${base(id)}/quality/proposals`,
      signal,
    ),
  analyze: (id: string, run: string) =>
    api.post<QualityProposal>(
      `${base(id)}/quality/runs/${encodeURIComponent(run)}/analyze`,
    ),
  doctor: (id: string, signal?: AbortSignal) =>
    api.get<DoctorReport>(`${base(id)}/quality/doctor`, signal),
  review: (
    id: string,
    proposal: QualityProposal,
    resolution: "accepted" | "rejected",
    options?: { patch_id?: string; regression_case_ids?: string[] },
  ) =>
    api.post<QualityProposal>(
      `${base(id)}/quality/proposals/${encodeURIComponent(proposal.id)}/review`,
      {
        expected_revision:
          proposal.application?.status === "pending"
            ? (proposal.review_inputs?.revision ?? proposal.revision)
            : proposal.revision,
        resolution,
        ...(proposal.application?.status === "pending" && proposal.review_inputs
          ? {
              patch_id: proposal.review_inputs.patch_id,
              regression_case_ids: proposal.review_inputs.regression_case_ids,
            }
          : (options ?? {})),
      },
    ),
};
