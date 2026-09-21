import { api, apiBase, authHeaders } from "@/lib/api-client";
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
  policy: "auto_read_only" | "ask_every_tool";
  semantic_model_id: string | null;
  /** All bound semantic models. The scalar mirrors the first entry. */
  semantic_model_ids: string[];
  visibility: "private" | "shared";
  created_at: string;
  updated_at: string;
};

export type AgentCreateInput = Partial<
  Omit<Agent, "agent_id" | "owner_name" | "created_at" | "updated_at">
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
};

export const agentsApi = {
  list: () => api.get<{ agents: Agent[]; count: number }>("/agents"),
  get: (id: string) => api.get<Agent>(`/agents/${encodeURIComponent(id)}`),
  create: (body: AgentCreateInput) => api.post<Agent>("/agents", body),
  update: (id: string, body: Partial<AgentCreateInput>) =>
    api.put<Agent>(`/agents/${encodeURIComponent(id)}`, body),
  remove: (id: string) => api.delete<void>(`/agents/${encodeURIComponent(id)}`),

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
};

/**
 * Opens one agent turn as an SSE stream. Same transport as the assistant turn
 * (fetch + ReadableStream, bearer token, no auto-reconnect), against the
 * agent-scoped endpoint.
 */
export async function streamAgentTurn(
  agentId: string,
  threadId: string,
  content: string,
  { signal, onEvent, role, model, providerId }: StreamAgentTurnOptions,
): Promise<void> {
  const response = await fetch(
    `${apiBase()}/agents/${encodeURIComponent(agentId)}/threads/${encodeURIComponent(threadId)}/messages`,
    {
      method: "POST",
      headers: authHeaders({
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      }),
      body: JSON.stringify({ content, role, model, provider_id: providerId }),
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

  let activeRun: string | undefined;
  let lastSequence = -1;
  for await (const frame of readSseFrames(response.body)) {
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
    onEvent(event);
  }
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
  agents: { name: string; description: string; agent_id: string }[];
  skills: { name: string; description: string }[];
  tools: {
    name: string;
    description: string;
    source: string;
    is_enabled: boolean;
  }[];
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
  settings: () => api.get<StudioSettings>("/agents/studio/settings"),
  updateSettings: (body: Partial<StudioPreferences>) =>
    api.patch<StudioPreferences>("/agents/studio/settings", body),
  capabilities: () =>
    api.get<StudioCapabilities>("/agents/studio/capabilities"),
};

// ── Skill Registry ─────────────────────────────────────────────

export type Skill = {
  skill_id: string;
  owner_name: string;
  name: string;
  description: string;
  body: string;
  scope: "user" | "global";
  created_at: string;
  updated_at: string;
};

export const skillsApi = {
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
  | { kind: "reasoning"; phase: string; text: string }
  | {
      kind: "tool";
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
    }
  | { kind: "answer" }
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
  | { kind: "context"; dropped_turns?: number; cleared_tool_results?: number };

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
