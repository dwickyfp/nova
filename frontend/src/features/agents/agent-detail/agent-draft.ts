import type { Agent, AgentCreateInput } from "../api";

/** The editable configuration of an agent, as a draft version saves it. */
export function toAgentDraft(agent: Agent): AgentCreateInput {
  const {
    agent_id: _agentId,
    owner_name: _ownerName,
    created_at: _createdAt,
    updated_at: _updatedAt,
    compiled_instructions: _compiledInstructions,
    config_revision: _configRevision,
    semantic_model_id: _semanticModelId,
    semantic_model_ids: _semanticModelIds,
    ...draft
  } = agent;
  return {
    ...draft,
    default_tools: (agent.default_tools ?? []).filter((name) => name !== "query_execute"),
    semantic_view_ids: agent.semantic_view_ids ?? [],
    harness_mode: "auto",
  };
}
