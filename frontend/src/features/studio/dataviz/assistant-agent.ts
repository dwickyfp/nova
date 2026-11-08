import type { Agent } from "@/features/agents/api";

/**
 * Nova's Dataviz assistant. It is not one of the user's agents and is never
 * chosen: the server assembles it per turn from the open dashboard, the
 * business ontology and what the active role may read
 * (backend/app/modules/agents/dataviz_assistant.py).
 */
export const DATAVIZ_ASSISTANT_ID = "nova-dataviz-assistant";

export const DATAVIZ_ASSISTANT: Agent = {
  agent_id: DATAVIZ_ASSISTANT_ID,
  owner_name: "",
  database_name: null,
  schema_name: null,
  name: "Nova Dataviz assistant",
  description: "Helps with the dashboards open in Dataviz.",
  avatar: null,
  color: null,
  model_provider_id: null,
  model_name: null,
  instructions_response: "",
  instructions_orchestration: "",
  response_style: null,
  sample_questions: [],
  budget_seconds: null,
  budget_tokens: null,
  tool_not_accessible: "accept",
  default_tools: [],
  default_skills: [],
  policy: "auto_read_only",
  semantic_model_id: null,
  semantic_model_ids: [],
  visibility: "private",
  created_at: "2026-11-08T00:00:00",
  updated_at: "2026-11-08T00:00:00",
};
