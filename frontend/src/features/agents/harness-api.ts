import { api } from "@/lib/api-client";
import type { HarnessGuardRule, HarnessPolicy } from "./api";

const base = (id: string) => `/agents/${encodeURIComponent(id)}`;

export type HarnessComponentKey =
  | "instructions"
  | "standing_rules"
  | "tool_guidance"
  | "skills"
  | "procedures"
  | "guards"
  | "memory"
  | "budget";

export type HarnessComponent = {
  key: HarnessComponentKey;
  load: "always" | "on_demand" | "in_loop" | "retrieved" | "every_turn";
  count: number | null;
  chars?: number;
  names?: string[];
  default?: string[];
  discoverable?: string[];
  platform?: { rule_id: string; stage: string; description: string }[];
  agent?: HarnessGuardRule[];
  profile?: string;
  max_iterations?: number;
  time_budget_seconds?: number;
  max_calls_per_tool?: number;
  spend_tokens?: number;
};

export type HarnessIssue = {
  severity: "error" | "warning";
  component: string;
  message: string;
};

export type HarnessSurface = {
  components: HarnessComponent[];
  always_on: {
    harness_chars: number;
    harness_cap: number;
    contract_chars: number;
    contract_cap: number;
  };
  tools: string[];
  policy: HarnessPolicy;
  issues: HarnessIssue[];
};

export type EditRow = {
  candidate: "inv" | "dom" | "compose";
  components: string[];
  notes: string;
  improved: string[];
  regressed: string[];
  passes: number;
  assertions: number;
  verdict:
    | "helped"
    | "hurt"
    | "no observed effect"
    | "confounded"
    | "kept baseline"
    | "no evidence";
  fell_back: boolean;
  adopted: boolean;
};

export type LedgerEntry = {
  stage: string;
  kind: "rollout" | "session";
  total_tokens: number;
  duration_ms: number | null;
  attempts: number;
  reused: boolean;
};

export type HarnessLesson = {
  id: string;
  experiment_id: string;
  cue: string;
  lesson: string;
  evidence: string;
  confidence: "high" | "medium" | "low";
  components: string[];
  created_at: string;
  retired?: boolean;
};

export type HarnessExperiment = {
  id: string;
  status: "running" | "passed" | "unavailable";
  version_id: string;
  cases: { id: string; name?: string }[];
  stages: Record<string, { stage_duration_ms?: number; total_tokens?: number }>;
  inflight?: string | null;
  total_tokens: number;
  max_tokens?: number;
  recommended_version_id: string;
  selected_label?: "baseline" | "inv" | "dom" | "compose";
  rationale?: string;
  edit_rows?: EditRow[];
  ledger?: LedgerEntry[];
  lesson?: Omit<HarnessLesson, "id" | "experiment_id" | "components" | "created_at"> | null;
  error?: string;
  error_class?: string;
  diagnosis?: string;
  accounting_gaps?: string[];
  started_at?: string;
  finished_at?: string;
  active?: boolean;
  interrupted?: boolean;
};

/** The order an experiment runs its stages in. */
export const EXPERIMENT_STAGES = [
  ["baseline", "Baseline run"],
  ["propose_inv", "Propose: how the agent works"],
  ["propose_dom", "Propose: data knowledge"],
  ["candidate_inv", "Run the working candidate"],
  ["candidate_dom", "Run the knowledge candidate"],
  ["propose_compose", "Compose from the evidence"],
  ["candidate_compose", "Run the composed candidate"],
  ["reflect", "Write the lesson"],
] as const;

export const harnessApi = {
  surface: (id: string, signal?: AbortSignal) =>
    api.get<HarnessSurface>(`${base(id)}/harness`, signal),
  experiments: (id: string, signal?: AbortSignal) =>
    api.get<{ experiments: HarnessExperiment[] }>(
      `${base(id)}/quality/harness-experiments`,
      signal,
    ),
  start: (
    id: string,
    body: {
      operation_id: string;
      version_id: string;
      case_ids: string[];
      max_tokens: number;
      max_seconds: number;
    },
  ) =>
    api.post<HarnessExperiment>(`${base(id)}/quality/harness-experiments`, body),
  lessons: (id: string, signal?: AbortSignal) =>
    api.get<{ lessons: HarnessLesson[] }>(
      `${base(id)}/quality/harness-lessons`,
      signal,
    ),
  retireLesson: (id: string, lesson: string) =>
    api.post<HarnessLesson>(
      `${base(id)}/quality/harness-lessons/${encodeURIComponent(lesson)}/retire`,
    ),
};
