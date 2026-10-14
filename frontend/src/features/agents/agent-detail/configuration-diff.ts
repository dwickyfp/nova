import type { Agent, AgentCreateInput } from "../api";

const labels: Record<string, string> = {
  name: "Name",
  description: "Description",
  instructions_response: "Response instructions",
  instructions_orchestration: "Orchestration instructions",
  model_provider_id: "AI provider",
  model_name: "Model",
  default_tools: "Tools",
  default_skills: "Default skills",
  discoverable_skills: "Available skills",
  semantic_view_ids: "Semantic Views",
  resource_bindings: "Resource bindings and fixed filters",
  policy: "Tool approval policy",
  budget_seconds: "Time budget",
  budget_tokens: "Context budget",
  visibility: "Visibility",
};

function stable(value: unknown): string {
  if (value === undefined || value === null) return "Not set";
  if (typeof value === "string") return value || "Empty";
  return JSON.stringify(
    value,
    (_key, item) =>
      item && typeof item === "object" && !Array.isArray(item)
        ? Object.fromEntries(
            Object.entries(item).sort(([a], [b]) => a.localeCompare(b)),
          )
        : item,
    2,
  );
}

export function configurationDiff(current: Agent, saved: AgentCreateInput) {
  return Object.entries(saved)
    .filter(
      ([key, value]) => stable(current[key as keyof Agent]) !== stable(value),
    )
    .map(([key, value]) => ({
      key,
      label: labels[key] ?? key.replace(/_/g, " "),
      before: stable(current[key as keyof Agent]),
      after: stable(value),
    }));
}
