import { api, apiBase, authHeaders } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import {
  parseAssistantEvent,
  readSseFrames,
} from "@/features/assistant/events";
import type { AssistantEvent } from "@/features/assistant/types";

/** Agent Studio API client (Phase 12). Mirrors /api/v1/agents/*. */

export type Agent = {
  agent_id: string;
  owner_name: string;
  database_name: string | null;
  schema_name: string | null;
  name: string;
  description: string;
  avatar: string | null;
  color: string | null;
  model_provider_id: string | null;
  model_name: string | null;
  instructions_response: string;
  instructions_orchestration: string;
  response_style: string | null;
  sample_questions: string[];
  budget_seconds: number | null;
  budget_tokens: number | null;
  tool_not_accessible: string;
  default_tools: string[];
  default_skills: string[];
  discoverable_skills?: string[];
  compiled_instructions?: Record<string, unknown>;
  harness_mode?: "auto" | "fast" | "guided" | "strict";
  policy: "auto_read_only" | "ask_every_tool";
  semantic_model_id: string | null;
  /** All bound semantic models. The scalar mirrors the first entry. */
  semantic_model_ids: string[];
  visibility: "private" | "shared";
  created_at: string;
  updated_at: string;
};

export type AgentMemory = {
  memory_id: string;
  user_name: string;
  agent_id: string;
  role_name: string;
  fact_key: string;
  fact: string;
  source_quote: string;
  source_thread_id: string;
  created_at: string;
  updated_at: string;
};

export type RuleProposal = {
  proposal_id: string;
  agent_id: string;
  memory_id: string;
  semantic_model_id: string;
  metric_name: string;
  prior_expression: string;
  proposed_expression: string;
  status: "pending" | "approved" | "rejected";
  previewed_at: string | null;
  created_at: string;
};

export type RuleProposalPreview = {
  proposal_id: string;
  metric_name: string;
  prior_sql: string;
  proposed_sql: string;
  prior_value: string | null;
  proposed_value: string | null;
  model_fingerprint: string;
};

export type AgentCreateInput = Partial<
  Omit<
    Agent,
    | "agent_id"
    | "owner_name"
    | "created_at"
    | "updated_at"
    | "compiled_instructions"
  >
> & {
  name: string;
};

export type SemanticModel = {
  semantic_model_id: string;
  owner_name: string;
  name: string;
  description: string;
  database_name: string | null;
  schema_name: string | null;
  ossie_version: string;
  definition: Record<string, unknown>;
  source_file_id: string | null;
  created_at: string;
  updated_at: string;
};

export type SemanticValidateResult = {
  valid: boolean;
  ossie_version: string | null;
  errors: string[];
  warnings: string[];
  dataset_count: number;
  metric_count: number;
  relationship_count: number;
};

export type SemanticPreview = {
  semantic_model_id: string;
  model_fingerprint: string;
  semantic_plan: Record<string, unknown>;
  generated_sql: string;
  confidence: Record<string, unknown>;
  relationship_path: string[];
  warnings: string[];
};

export type SemanticLintResult = {
  semantic_model_id: string;
  model_fingerprint: string;
  valid: boolean;
  errors: string[];
  findings: Array<{
    code: string;
    severity: string;
    message: string;
    object_name: string | null;
  }>;
  quality: Record<string, unknown>;
};

export type SemanticQualityLabResult = {
  semantic_model_id: string;
  model_fingerprint: string;
  total: number;
  matched: number;
  changed: number;
  cases: Array<{
    verified_query_id: string;
    question: string;
    status: "matched" | "changed";
  }>;
};

export type VerifiedQuery = {
  verified_query_id: string;
  semantic_model_id: string;
  model_fingerprint: string;
  question: string;
  semantic_plan: Record<string, unknown>;
  verified_sql: string;
  expected_result_signature: string | null;
  verified_by: string;
  verified_at: string;
  tags: string[];
  usage_count: number;
  success_count: number;
};

export type AgentThread = {
  thread_id: string;
  title: string;
  workspace_file_id: string | null;
  agent_id: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
};

export type AgentMessage = {
  message_id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  created_at: string;
  /**
   * The assistant turn's recorded trace, so a reopened conversation can rebuild
   * its process view. Redacted by the loop before it was stored.
   */
  steps?: TraceStep[];
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  model_name?: string | null;
  feedback?: "like" | "dislike" | null;
  attachments?: { name: string; size_bytes: number; media_type?: string }[];
};

export const agentsApi = {
  list: () => api.get<{ agents: Agent[]; count: number }>("/agents"),
  get: (id: string) => api.get<Agent>(`/agents/${encodeURIComponent(id)}`),
  create: (body: AgentCreateInput) => api.post<Agent>("/agents", body),
  update: (id: string, body: Partial<AgentCreateInput>) =>
    api.put<Agent>(`/agents/${encodeURIComponent(id)}`, body),
  remove: (id: string) => api.delete<void>(`/agents/${encodeURIComponent(id)}`),
  listMemories: (agentId: string, offset = 0) =>
    api.get<{ memories: AgentMemory[]; count: number; next_offset: number | null }>(
      `/agents/${encodeURIComponent(agentId)}/memories?offset=${offset}&limit=100`,
    ),
  deleteMemory: (agentId: string, memoryId: string) =>
    api.delete<void>(
      `/agents/${encodeURIComponent(agentId)}/memories/${encodeURIComponent(memoryId)}`,
    ),
  listRuleProposals: (modelId: string) =>
    api.get<{ proposals: RuleProposal[]; count: number }>(
      `/agents/semantic-models/${encodeURIComponent(modelId)}/rule-proposals`,
    ),
  createRuleProposal: (modelId: string, body: {
    agent_id: string;
    memory_id: string;
    metric_name: string;
    proposed_expression: string;
  }) => api.post<RuleProposal>(
    `/agents/semantic-models/${encodeURIComponent(modelId)}/rule-proposals`, body,
  ),
  previewRuleProposal: (modelId: string, proposalId: string) =>
    api.post<RuleProposalPreview>(
      `/agents/semantic-models/${encodeURIComponent(modelId)}/rule-proposals/${encodeURIComponent(proposalId)}/preview`, {},
    ),
  approveRuleProposal: (modelId: string, proposalId: string) =>
    api.post<RuleProposal>(
      `/agents/semantic-models/${encodeURIComponent(modelId)}/rule-proposals/${encodeURIComponent(proposalId)}/approve`, {},
    ),
  rejectRuleProposal: (modelId: string, proposalId: string) =>
    api.post<RuleProposal>(
      `/agents/semantic-models/${encodeURIComponent(modelId)}/rule-proposals/${encodeURIComponent(proposalId)}/reject`, {},
    ),

  listSemanticModels: () =>
    api.get<{ models: SemanticModel[]; count: number }>(
      "/agents/semantic-models",
    ),
  getSemanticModel: (id: string) =>
    api.get<SemanticModel>(`/agents/semantic-models/${encodeURIComponent(id)}`),
  createSemanticModel: (body: {
    name: string;
    description?: string;
    database_name?: string | null;
    schema_name?: string | null;
    definition: string;
    source_file_id?: string | null;
  }) => api.post<SemanticModel>("/agents/semantic-models", body),
  deleteSemanticModel: (id: string) =>
    api.delete<void>(`/agents/semantic-models/${encodeURIComponent(id)}`),
  validateSemanticModel: (definition: string) =>
    api.post<SemanticValidateResult>("/agents/semantic-models/validate", {
      definition,
    }),
  previewSemanticQuestion: (id: string, question: string) =>
    api.post<SemanticPreview>(
      `/agents/semantic-models/${encodeURIComponent(id)}/preview`,
      { question },
    ),
  lintSemanticModel: (id: string) =>
    api.get<SemanticLintResult>(
      `/agents/semantic-models/${encodeURIComponent(id)}/lint`,
    ),
  runSemanticQualityLab: (id: string) =>
    api.get<SemanticQualityLabResult>(
      `/agents/semantic-models/${encodeURIComponent(id)}/quality-lab`,
    ),
  listVerifiedQueries: (id: string) =>
    api.get<{ queries: VerifiedQuery[]; count: number }>(
      `/agents/semantic-models/${encodeURIComponent(id)}/verified-queries`,
    ),
  createVerifiedQuery: (
    id: string,
    body: {
      question: string;
      semantic_plan: Record<string, unknown>;
      verified_sql: string;
      expected_result_signature?: string | null;
      tags?: string[];
    },
  ) =>
    api.post<VerifiedQuery>(
      `/agents/semantic-models/${encodeURIComponent(id)}/verified-queries`,
      body,
    ),

  listThreads: (agentId: string) =>
    api.get<{ threads: AgentThread[]; count: number }>(
      `/agents/${encodeURIComponent(agentId)}/threads`,
    ),
  createThread: (agentId: string, title?: string) =>
    api.post<AgentThread>(`/agents/${encodeURIComponent(agentId)}/threads`, {
      title,
    }),
  getThread: (agentId: string, threadId: string) =>
    api.get<{ thread: AgentThread; messages: AgentMessage[] }>(
      `/agents/${encodeURIComponent(agentId)}/threads/${encodeURIComponent(threadId)}`,
    ),
  deleteThread: (agentId: string, threadId: string) =>
    api.delete<void>(
      `/agents/${encodeURIComponent(agentId)}/threads/${encodeURIComponent(threadId)}`,
    ),
  setMessageFeedback: (
    agentId: string,
    threadId: string,
    messageId: string,
    feedback: "like" | "dislike" | null,
  ) =>
    api.put<{ feedback: "like" | "dislike" | null }>(
      `/agents/${encodeURIComponent(agentId)}/threads/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(messageId)}/feedback`,
      { feedback },
    ),
  renameThread: (agentId: string, threadId: string, title: string) =>
    api.put<AgentThread>(
      `/agents/${encodeURIComponent(agentId)}/threads/${encodeURIComponent(threadId)}`,
      { title },
    ),
  decideToolCall: (
    agentId: string,
    toolCallId: string,
    decision: "allow_once" | "allow_session" | "deny",
  ) =>
    api.post<{
      tool_call_id: string;
      status: string;
      grant_active: boolean;
    }>(
      `/agents/${encodeURIComponent(agentId)}/tool-calls/${encodeURIComponent(toolCallId)}/decision`,
      { decision },
    ),
};

export type StreamAgentTurnOptions = {
  signal?: AbortSignal;
  onEvent: (event: AssistantEvent) => void;
  role?: string | null;
  model?: string | null;
  providerId?: string | null;
  attachments?: { name: string; content: string; media_type: string }[];
  onAccepted?: () => void;
};

/**
 * Opens one agent turn as an SSE stream. Same transport as the assistant turn
 * (fetch + ReadableStream, bearer token) against the agent-scoped endpoint.
 * A dropped connection replays journaled frames by run ID and sequence without
 * posting the user message or running a tool a second time.
 */
export async function streamAgentTurn(
  agentId: string,
  threadId: string,
  content: string,
  { signal, onEvent, role, model, providerId, attachments, onAccepted }: StreamAgentTurnOptions,
): Promise<void> {
  const response = await fetch(
    `${apiBase()}/agents/${encodeURIComponent(agentId)}/threads/${encodeURIComponent(threadId)}/messages`,
    {
      method: "POST",
      headers: authHeaders({
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      }),
      body: JSON.stringify({ content, role, model, provider_id: providerId, attachments }),
      signal,
    },
  );

  if (response.status === 401) throw new Error("Session expired");
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined);
    throw new Error(detail || "The agent request failed");
  }
  if (!response.body) throw new Error("The agent returned an empty stream");
  onAccepted?.();

  let activeRun: string | undefined = response.headers.get("X-Nova-Run-ID") || undefined;
  let lastSequence = -1;
  let finished = false;
  async function consume(body: ReadableStream<Uint8Array>) {
    for await (const frame of readSseFrames(body)) {
      const event = parseAssistantEvent(frame.event, frame.data);
      if (!event) continue;
      if (event.run_id && event.run_id !== activeRun) {
        activeRun = event.run_id;
        lastSequence = -1;
      }
      if (event.sequence !== undefined) {
        if (event.sequence <= lastSequence) continue;
        lastSequence = event.sequence;
      }
      if (event.type === "role_changed") {
        const auth = useAuthStore.getState().auth;
        if (auth.user)
          auth.setUser({ ...auth.user, activeRole: event.active_role });
      }
      if (event.type === "done") finished = true;
      onEvent(event);
    }
  }
  try {
    await consume(response.body);
  } catch (error) {
    if (signal?.aborted) throw error;
  }
  for (let retry = 0; !finished && !signal?.aborted && activeRun && retry < 3; retry++) {
    try {
      const replay = await fetch(
        `${apiBase()}/agents/${encodeURIComponent(agentId)}/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(activeRun)}/events?after=${lastSequence}`,
        { headers: authHeaders({ Accept: "text/event-stream" }), signal },
      );
      if (!replay.ok || !replay.body) break;
      await consume(replay.body);
    } catch (error) {
      if (signal?.aborted) throw error;
      if (retry === 2) break;
    }
  }
  if (!finished && !signal?.aborted) throw new Error("The agent run ended before completion");
}

// ── Tools Registry ─────────────────────────────────────────────

export type Tool = {
  tool_id: string;
  owner_name: string;
  name: string;
  description: string;
  source: string;
  input_schema: Record<string, unknown>;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type McpServer = {
  server_id: string;
  owner_name: string;
  name: string;
  description: string;
  transport: "http" | "sse" | "stdio";
  endpoint: string | null;
  command: string | null;
  args: string[];
  is_active: boolean;
  last_status: string | null;
  created_at: string;
  updated_at: string;
};

export type McpDiscoverResult = {
  ok: boolean;
  status: string;
  tools_discovered: number;
  error: string | null;
};

// ── Studio ─────────────────────────────────────────────────────

export type StudioIdentity = {
  username: string;
  roles: string[];
  active_role: string | null;
  warehouses: string[];
  active_warehouse: string | null;
};

export type StudioPreferences = {
  theme: "light" | "dark" | "system";
  language: string;
  preferred_name: string | null;
  role: string | null;
  warehouse: string | null;
  extended_thinking: boolean;
};

export type StudioSettings = {
  identity: StudioIdentity;
  preferences: StudioPreferences;
};

export type StudioCapabilities = {
  connectors: {
    server_id: string;
    name: string;
    description: string;
    is_active: boolean;
    last_status: string | null;
  }[];
  agents: { name: string; description: string; agent_id: string }[];
  skills: { name: string; description: string; source: "builtin" | "user" }[];
  tools: {
    name: string;
    description: string;
    source: string;
    is_enabled: boolean;
  }[];
};

export type StudioArtifact = {
  artifact_id: string;
  title: string;
  artifact_type: "chart" | "table";
  agent_id: string | null;
  thread_id: string | null;
  database_name: string | null;
  schema_name: string | null;
  created_at: string;
  updated_at: string;
};

export type StudioArtifactDetail = StudioArtifact & {
  sql_text: string;
  chart_spec: Record<string, unknown> | null;
};

export type StudioArtifactCreate = {
  title: string;
  artifact_type: "chart" | "table";
  sql_text: string;
  database_name?: string | null;
  schema_name?: string | null;
  chart_spec?: Record<string, unknown> | null;
  agent_id?: string | null;
  thread_id?: string | null;
};

export type StudioArtifactRefresh = {
  artifact: StudioArtifactDetail;
  columns: string[];
  rows: (string | number | null)[][];
  row_count: number;
  elapsed_ms: number;
};

export type StudioDashboardTile = {
  tile_id: string;
  artifact_id: string;
  x: number;
  y: number;
  w: number;
  h: number;
};

export type StudioDashboard = {
  dashboard_id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type StudioDashboardDetail = StudioDashboard & {
  layout: { tiles: StudioDashboardTile[] };
};

export type StudioArtifactDraft = {
  sql_text: string;
  artifact_type: "chart" | "table";
  chart_spec: Record<string, unknown> | null;
};

export type StudioArtifactEditMessage = {
  role: "user" | "assistant";
  content: string;
};

export type StudioArtifactEditResponse = {
  message: string;
  draft: StudioArtifactDraft | null;
  columns: string[];
  rows: (string | number | null)[][];
  row_count: number;
  elapsed_ms: number;
};

export const toolsApi = {
  list: () => api.get<{ tools: Tool[]; count: number }>("/agents/tools"),
  create: (body: { name: string; description?: string; source?: string }) =>
    api.post<Tool>("/agents/tools", body),
  toggle: (id: string, isEnabled: boolean) =>
    api.patch<Tool>(`/agents/tools/${encodeURIComponent(id)}`, {
      is_enabled: isEnabled,
    }),
  remove: (id: string) =>
    api.delete<void>(`/agents/tools/${encodeURIComponent(id)}`),
};

export const mcpApi = {
  list: () =>
    api.get<{ servers: McpServer[]; count: number }>("/agents/mcp-servers"),
  create: (body: {
    name: string;
    description?: string;
    transport: "http" | "sse" | "stdio";
    endpoint?: string | null;
    command?: string | null;
    args?: string[];
    is_active?: boolean;
  }) => api.post<McpServer>("/agents/mcp-servers", body),
  update: (
    id: string,
    body: Partial<
      Omit<McpServer, "server_id" | "owner_name" | "created_at" | "updated_at">
    >,
  ) =>
    api.patch<McpServer>(`/agents/mcp-servers/${encodeURIComponent(id)}`, body),
  remove: (id: string) =>
    api.delete<void>(`/agents/mcp-servers/${encodeURIComponent(id)}`),
  discover: (id: string) =>
    api.post<McpDiscoverResult>(
      `/agents/mcp-servers/${encodeURIComponent(id)}/discover`,
    ),
};

export const studioApi = {
  skillAuthor: () => api.post<Agent>("/agents/studio/skill-author"),
  settings: () => api.get<StudioSettings>("/agents/studio/settings"),
  updateSettings: (body: Partial<StudioPreferences>) =>
    api.patch<StudioPreferences>("/agents/studio/settings", body),
  capabilities: () =>
    api.get<StudioCapabilities>("/agents/studio/capabilities"),
  listArtifacts: () =>
    api.get<{ artifacts: StudioArtifact[]; count: number }>(
      "/agents/studio/artifacts",
    ),
  createArtifact: (body: StudioArtifactCreate) =>
    api.post<StudioArtifactDetail>("/agents/studio/artifacts", body),
  getArtifact: (id: string) =>
    api.get<StudioArtifactDetail>(
      `/agents/studio/artifacts/${encodeURIComponent(id)}`,
    ),
  refreshArtifact: (id: string) =>
    api.post<StudioArtifactRefresh>(
      `/agents/studio/artifacts/${encodeURIComponent(id)}/refresh`,
    ),
  editArtifact: (
    id: string,
    body: {
      instruction: string;
      draft: StudioArtifactDraft | null;
      columns: string[];
      history: StudioArtifactEditMessage[];
    },
  ) =>
    api.post<StudioArtifactEditResponse>(
      `/agents/studio/artifacts/${encodeURIComponent(id)}/edit`,
      body,
    ),
  applyArtifactEdit: (
    id: string,
    body: { draft: StudioArtifactDraft; expected_updated_at: string },
  ) =>
    api.patch<StudioArtifactRefresh>(
      `/agents/studio/artifacts/${encodeURIComponent(id)}`,
      body,
    ),
  deleteArtifact: (id: string) =>
    api.delete<void>(`/agents/studio/artifacts/${encodeURIComponent(id)}`),
  listDashboards: () =>
    api.get<{ dashboards: StudioDashboard[]; count: number }>(
      "/agents/studio/dashboards",
    ),
  createDashboard: (title: string) =>
    api.post<StudioDashboardDetail>("/agents/studio/dashboards", { title }),
  getDashboard: (id: string) =>
    api.get<StudioDashboardDetail>(
      `/agents/studio/dashboards/${encodeURIComponent(id)}`,
    ),
  updateDashboard: (
    id: string,
    body: {
      title: string;
      layout: { tiles: StudioDashboardTile[] };
      expected_updated_at: string;
    },
  ) =>
    api.put<StudioDashboardDetail>(
      `/agents/studio/dashboards/${encodeURIComponent(id)}`,
      body,
    ),
  deleteDashboard: (id: string) =>
    api.delete<void>(`/agents/studio/dashboards/${encodeURIComponent(id)}`),
};

// ── Skill Registry ─────────────────────────────────────────────

export type Skill = {
  skill_id: string;
  owner_name: string;
  name: string;
  description: string;
  body: string;
  scope: "user" | "global";
  source: "builtin" | "user";
  read_only: boolean;
  created_at: string;
  updated_at: string;
};

export const skillsApi = {
  verify: (document: string) =>
    api.post<{ name: string; description: string }>(
      "/agents/studio/skills/verify",
      { document },
    ),
  personal: () =>
    api.get<{ skills: Skill[]; count: number }>("/agents/studio/skills"),
  upload: (document: string) =>
    api.post<Skill>("/agents/studio/skills", { document }),
  update: (id: string, document: string) =>
    api.put<Skill>(`/agents/studio/skills/${encodeURIComponent(id)}`, {
      document,
    }),
  list: () => api.get<{ skills: Skill[]; count: number }>("/agents/skills"),
  create: (body: {
    name: string;
    description?: string;
    body?: string;
    scope?: "user" | "global";
  }) => api.post<Skill>("/agents/skills", body),
  remove: (id: string) =>
    api.delete<void>(`/agents/skills/${encodeURIComponent(id)}`),
};

// ── Agent access roles + Verify Access ─────────────────────────

export type AgentRole = { role_name: string; grant_type: string };

export type AccessCheckItem = {
  kind: string;
  name: string;
  granted: boolean;
  detail: string;
};

export type AccessCheckResult = {
  role_name: string;
  all_granted: boolean;
  items: AccessCheckItem[];
  checked_at: string;
};

// ── Custom tools ───────────────────────────────────────────────

export type CustomTool = {
  tool_id: string;
  owner_name: string;
  name: string;
  description: string;
  kind: "function" | "procedure";
  database_name: string | null;
  function_name: string | null;
  definition: {
    args?: string[];
    parameters?: {
      name: string;
      type: string;
      description?: string;
      required?: boolean;
    }[];
    statements?: string[];
    /** 'result' returns rows to the model; 'run' executes with no return. */
    output_mode?: "result" | "run";
  };
  created_at: string;
  updated_at: string;
};

// ── Observability ──────────────────────────────────────────────

export type UsageSummary = {
  total_sessions: number;
  total_tokens: number;
  total_active_users: number;
  series: { date: string; sessions: number; tokens: number }[];
};

export type SessionRow = {
  thread_id: string;
  title: string;
  user_name: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  total_tokens: number;
  first_input: string;
};

/** One step in an assistant turn's trace, as recorded by the loop. */
export type TraceStep =
  | {
      kind: "reasoning";
      phase: string;
      text: string;
      status?: "running" | "done" | "failed";
      step_id?: string;
      started_offset_ms?: number;
      duration_ms?: number;
    }
  | {
      /** One provider request. It exposes timing and purpose, never hidden CoT. */
      kind: "provider";
      step_id?: string;
      purpose: "planning" | "response";
      status: "running" | "done" | "failed";
      started_offset_ms?: number;
      duration_ms?: number;
      error?: string;
    }
  | {
      kind: "tool";
      step_id?: string;
      tool_call_id?: string;
      name: string;
      preview: string;
      arguments: Record<string, string>;
      status: "running" | "done" | "failed";
      error?: string;
      /** What the tool did, for a reader who opens the step. Redacted. */
      detail?: string;
      stage?: string;
      status_text?: string;
      started_offset_ms?: number;
      duration_ms?: number;
      progress?: {
        stage: string;
        text: string;
        at_offset_ms?: number;
        duration_ms?: number;
      }[];
      /** Tool-specific, bounded, credential-free observability payload. */
      trace_detail?: Record<string, unknown>;
      /** Evidence accepted by Nova's verifier for final composition. */
      evidence_id?: string;
    }
  | {
      kind: "runtime_decision";
      step_id?: string;
      intent: string;
      harness_mode: "fast" | "guided" | "strict";
      selected_tools: string[];
      selected_skills: string[];
      semantic_model_ids: string[];
      prompt_telemetry: Record<string, number>;
      status: "done";
      started_offset_ms?: number;
      duration_ms?: number;
    }
  | {
      kind: "state";
      step_id?: string;
      state: string;
      status: "done";
      started_offset_ms?: number;
      duration_ms?: number;
    }
  | {
      kind: "active_state";
      step_id?: string;
      state: Record<string, unknown>;
      status: "done";
      started_offset_ms?: number;
      duration_ms?: number;
    }
  | {
      kind: "answer";
      step_id?: string;
      started_offset_ms?: number;
      duration_ms?: number;
    }
  | {
      kind: "text";
      content_index: number;
      content_id?: string;
      text: string;
    }
  /** A result grid the turn rendered, recorded so a reload can show it again. */
  | {
      kind: "table";
      content_index?: number;
      content_id?: string;
      tool_call_id?: string;
      title?: string;
      columns: string[];
      rows: (string | number | null)[][];
    }
  /** A chart the turn rendered. `chart_spec` is Vega-Lite v5 JSON. */
  | {
      kind: "chart";
      content_index?: number;
      content_id?: string;
      tool_call_id: string;
      chart_spec: string;
    }
  /** Sources a semantic search cited. */
  | {
      kind: "citation";
      content_index?: number;
      content_id?: string;
      citations: { title?: string; source?: string; snippet?: string }[];
    }
  /** A context-management note (NOVA-124). */
  | {
      kind: "context";
      step_id?: string;
      dropped_turns?: number;
      cleared_tool_results?: number;
      started_offset_ms?: number;
      duration_ms?: number;
    };

export type ThreadTurn = {
  message_id: string;
  seq: number;
  role: string;
  content: string;
  model_name: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  created_at: string;
  /** The assistant turn's ordered trace; empty on user turns. */
  steps: TraceStep[];
  /** The system prompt sent to the model for this turn. */
  instructions: string | null;
};

export type ThreadTrace = {
  thread_id: string;
  title: string;
  user_name: string;
  agent_id: string | null;
  created_at: string;
  updated_at: string;
  total_tokens: number;
  turns: ThreadTurn[];
};

export const agentAccessApi = {
  list: (agentId: string) =>
    api.get<{ roles: AgentRole[]; count: number }>(
      `/agents/${encodeURIComponent(agentId)}/access`,
    ),
  add: (
    agentId: string,
    roleName: string,
    grantType: "USAGE" | "OWNERSHIP" = "USAGE",
  ) =>
    api.post<AgentRole>(`/agents/${encodeURIComponent(agentId)}/access`, {
      role_name: roleName,
      grant_type: grantType,
    }),
  remove: (agentId: string, roleName: string) =>
    api.delete<void>(
      `/agents/${encodeURIComponent(agentId)}/access/${encodeURIComponent(roleName)}`,
    ),
  verify: (agentId: string, roleName: string) =>
    api.post<AccessCheckResult>(
      `/agents/${encodeURIComponent(agentId)}/access/verify`,
      { role_name: roleName },
    ),
};

export const customToolsApi = {
  list: () =>
    api.get<{ tools: CustomTool[]; count: number }>("/agents/custom-tools"),
  create: (body: {
    name: string;
    description?: string;
    kind: "function" | "procedure";
    database_name?: string | null;
    function_name?: string | null;
    definition?: CustomTool["definition"];
  }) => api.post<CustomTool>("/agents/custom-tools", body),
  update: (id: string, body: Partial<CustomTool>) =>
    api.put<CustomTool>(`/agents/custom-tools/${encodeURIComponent(id)}`, body),
  remove: (id: string) =>
    api.delete<void>(`/agents/custom-tools/${encodeURIComponent(id)}`),
};

export const observabilityApi = {
  usage: (agentId: string, days = 7) =>
    api.get<UsageSummary>(
      `/agents/${encodeURIComponent(agentId)}/observability/usage?days=${days}`,
    ),
  sessions: (agentId: string, limit = 100) =>
    api.get<{ sessions: SessionRow[] }>(
      `/agents/${encodeURIComponent(agentId)}/observability/sessions?limit=${limit}`,
    ),
  trace: (agentId: string, threadId: string) =>
    api.get<ThreadTrace>(
      `/agents/${encodeURIComponent(agentId)}/observability/sessions/${encodeURIComponent(threadId)}`,
    ),
};

// ── Roles picker (Access dropdown) ─────────────────────────────

export const rolesApi = {
  list: () => api.get<{ roles: string[]; count: number }>("/agents/roles"),
};

// ── Custom tool parameters ─────────────────────────────────────

export type CustomToolParameter = {
  name: string;
  type: string;
  description: string;
  required: boolean;
};
