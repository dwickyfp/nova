import { api } from "@/lib/api-client";

const agentPath = (agentId: string, rest: string) =>
  `/agents/${encodeURIComponent(agentId)}${rest}`;

// ── Learning loop and readiness ───────────────────────────────

export type VerifiedQueryCandidate = {
  candidate_id: string;
  view_id: string;
  view_version: number | null;
  question: string;
  semantic_plan: Record<string, unknown>;
  status: "pending" | "approved" | "rejected";
  proposed_by: string;
  created_at: string;
};

export type ImprovementSuggestions = {
  feedback: { semantic_model_id: string; kind: string; count: number; examples: string[] }[];
  materialized_views: {
    semantic_model_id: string;
    metrics: string[];
    dimensions: string[];
    time_grain: string | null;
    query_count: number;
  }[];
};

export type ReadinessCheck = {
  id: string;
  status: "ok" | "warn" | "fail";
  title: string;
  detail: string;
  fix: string;
};

export type Readiness = { ready: boolean; score: number; checks: ReadinessCheck[] };

export const learningApi = {
  candidates: (agentId: string, status = "pending") =>
    api.get<{ candidates: VerifiedQueryCandidate[]; count: number }>(
      agentPath(agentId, `/verified-query-candidates?status=${encodeURIComponent(status)}`),
    ),
  decide: (agentId: string, candidateId: string, decision: "approve" | "reject") =>
    api.post<{ candidate_id: string; status: string; draft_version: number | null }>(
      agentPath(agentId, `/verified-query-candidates/${encodeURIComponent(candidateId)}/${decision}`),
    ),
  suggestions: (agentId: string) =>
    api.get<ImprovementSuggestions>(agentPath(agentId, "/improvement-suggestions")),
  readiness: (agentId: string) => api.get<Readiness>(agentPath(agentId, "/readiness")),
};

// ── Deep research ──────────────────────────────────────────────

export type DeepResearchRun = {
  run_id: string;
  agent_id: string;
  thread_id: string;
  question: string;
  status: "planning" | "running" | "writing" | "done" | "failed" | "cancelled" | "interrupted";
  plan: string[] | null;
  progress: { question: string; status: string; finish?: string | null }[] | null;
  report: string | null;
};

export const deepResearchApi = {
  start: (agentId: string, question: string) =>
    api.post<{ run_id: string; thread_id: string; status: string }>(
      agentPath(agentId, "/deep-research"), { question },
    ),
  get: (agentId: string, runId: string) =>
    api.get<DeepResearchRun>(agentPath(agentId, `/deep-research/${encodeURIComponent(runId)}`)),
  cancel: (agentId: string, runId: string) =>
    api.post<{ cancelling: boolean }>(
      agentPath(agentId, `/deep-research/${encodeURIComponent(runId)}/cancel`),
    ),
};
