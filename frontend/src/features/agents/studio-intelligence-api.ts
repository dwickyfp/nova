import { api, apiBase, authHeaders, ApiError } from "@/lib/api-client";

const agentPath = (agentId: string, rest: string) =>
  `/agents/${encodeURIComponent(agentId)}${rest}`;

// ── Automations ────────────────────────────────────────────────

export type AutomationCondition = {
  metric: string;
  operator: ">" | ">=" | "<" | "<=" | "=" | "!=";
  value: number;
};

export type AutomationDelivery = {
  mcp_tool_id?: string | null;
  text_argument?: string;
  fixed_arguments?: Record<string, string | number | boolean>;
};

export type Automation = {
  automation_id: string;
  agent_id: string;
  title: string;
  prompt: string;
  schedule_kind: "cron" | "interval";
  schedule_expr: string;
  timezone: string;
  condition: AutomationCondition | null;
  delivery: AutomationDelivery | null;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;
  last_thread_id: string | null;
};

export type AutomationInput = {
  title: string;
  prompt: string;
  schedule_kind: "cron" | "interval";
  schedule_expr: string;
  timezone?: string;
  condition?: AutomationCondition | null;
  delivery?: AutomationDelivery;
  enabled?: boolean;
};

export const automationsApi = {
  list: (agentId: string) =>
    api.get<{ automations: Automation[]; count: number }>(agentPath(agentId, "/automations")),
  create: (agentId: string, body: AutomationInput) =>
    api.post<Automation>(agentPath(agentId, "/automations"), body),
  update: (agentId: string, id: string, body: Partial<AutomationInput>) =>
    api.patch<Automation>(agentPath(agentId, `/automations/${encodeURIComponent(id)}`), body),
  remove: (agentId: string, id: string) =>
    api.delete<void>(agentPath(agentId, `/automations/${encodeURIComponent(id)}`)),
};

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

// ── Sharing ────────────────────────────────────────────────────

export type ShareTarget = { target_type: "user" | "role"; target_name: string };
export type Share = ShareTarget & {
  share_id: string;
  object_type: "thread" | "dashboard";
  object_id: string;
  owner_name: string;
};

export type SharedThread = {
  thread_id: string;
  title: string | null;
  owner_name: string;
  messages: {
    message_id: string;
    role: "user" | "assistant";
    content: string;
    steps: { kind?: string; name?: string; tool_call_id?: string;
      trace_detail?: { question?: string } }[];
  }[];
  note: string;
};

export type ResultRows = {
  columns: string[];
  rows: (string | number | null)[][];
  row_count: number;
};

export type SharedDashboard = {
  dashboard_id: string;
  title: string;
  owner_name: string;
  layout: { tiles: { tile_id: string; artifact_id: string; x: number; y: number; w: number; h: number }[] };
};

export type SharedTile = ResultRows & {
  artifact: { artifact_id: string; title: string; artifact_type: "chart" | "table";
    chart_spec: Record<string, unknown> | null };
};

export const sharingApi = {
  create: (objectType: Share["object_type"], objectId: string, target: ShareTarget) =>
    api.post<Share>("/agents/studio/shares", {
      object_type: objectType, object_id: objectId, ...target,
    }),
  list: (objectType: Share["object_type"], objectId: string) =>
    api.get<{ shares: Share[]; count: number }>(
      `/agents/studio/shares?object_type=${objectType}&object_id=${encodeURIComponent(objectId)}`,
    ),
  revoke: (shareId: string) =>
    api.delete<void>(`/agents/studio/shares/${encodeURIComponent(shareId)}`),
  sharedWithMe: () => api.get<{ shared: Share[]; count: number }>("/agents/studio/shared"),
  openThread: (threadId: string) =>
    api.get<SharedThread>(`/agents/studio/shared/threads/${encodeURIComponent(threadId)}`),
  openDashboard: (dashboardId: string) =>
    api.get<SharedDashboard>(`/agents/studio/shared/dashboards/${encodeURIComponent(dashboardId)}`),
  refreshTile: (dashboardId: string, artifactId: string) =>
    api.post<SharedTile>(
      `/agents/studio/shared/dashboards/${encodeURIComponent(dashboardId)}/artifacts/${encodeURIComponent(artifactId)}/refresh`,
    ),
  refreshResult: (threadId: string, toolCallId: string) =>
    api.post<ResultRows>(
      `/agents/studio/shared/threads/${encodeURIComponent(threadId)}/results/${encodeURIComponent(toolCallId)}/refresh`,
    ),
};

// ── Export ─────────────────────────────────────────────────────

export type ExportFormat = "csv" | "xlsx" | "pdf" | "pptx";

/** Download an artifact's current result, re-run with the caller's access. */
export async function downloadArtifact(artifactId: string, format: ExportFormat): Promise<void> {
  const response = await fetch(
    `${apiBase()}/agents/studio/artifacts/${encodeURIComponent(artifactId)}/export?format=${format}`,
    { headers: authHeaders() },
  );
  if (!response.ok) {
    let message = "The export failed.";
    try {
      const body = await response.json();
      message = typeof body?.detail === "string" ? body.detail : message;
    } catch {
      // keep the generic message
    }
    throw new ApiError(response.status, message);
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `result.${format}`;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
