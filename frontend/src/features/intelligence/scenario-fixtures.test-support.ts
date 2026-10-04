import type { ScenarioDefinition } from "./scenario-schema";

/** Reviewed test fixture for an operational model without monetary outcomes. */
export const capacityScenario: ScenarioDefinition = {
  id: "capacity-v1",
  scenario_kind: "capacity",
  version: 1,
  title: "Order capacity",
  description: "Compare conditional order capacity using stated assumptions.",
  target_metric: "orders",
  target_metric_policy: "named_metric",
  currency_required: false,
  action_types: ["capacity_upgrade", "recommendation"],
  constraints: ["nonnegative_capacity"],
  simulation_adapter: "capacity-v1",
  shared_input_schema: {
    type: "object",
    additionalProperties: false,
    required: ["baseline"],
    properties: {
      baseline: {
        type: "number",
        title: "Observed order capacity",
        minimum: 0,
      },
    },
  },
  input_schema: {
    type: "object",
    additionalProperties: false,
    required: ["action_type", "additional_capacity", "action_cost"],
    properties: {
      action_type: {
        type: "string",
        title: "Capacity action",
        enum: ["capacity_upgrade", "recommendation"],
        default: "capacity_upgrade",
      },
      additional_capacity: {
        type: "number",
        title: "Additional order capacity",
        minimum: 0,
      },
      action_cost: {
        type: "number",
        title: "Action cost",
        minimum: 0,
        default: 0,
      },
    },
  },
};
