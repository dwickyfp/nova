export type ScenarioValue = string | number | boolean;

export type ScenarioProperty = {
  type: "string" | "number" | "integer" | "boolean";
  title?: string;
  description?: string;
  unit?: string;
  enum?: ScenarioValue[];
  default?: ScenarioValue;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  minLength?: number;
  maxLength?: number;
};

export type ScenarioSchema = {
  type: "object";
  properties: Record<string, ScenarioProperty>;
  required?: string[];
  additionalProperties?: false;
};

export type ScenarioDefinition = {
  id: string;
  scenario_kind: string;
  version: number;
  title: string;
  description: string;
  input_schema: ScenarioSchema;
  shared_input_schema: ScenarioSchema;
  action_types?: string[];
  constraints?: string[];
  simulation_adapter?: string;
};

const propertyKeys = new Set([
  "type",
  "title",
  "description",
  "unit",
  "enum",
  "default",
  "minimum",
  "maximum",
  "exclusiveMinimum",
  "exclusiveMaximum",
  "minLength",
  "maxLength",
]);
const forbiddenNames = new Set(["__proto__", "prototype", "constructor"]);

function valueMatches(
  type: ScenarioProperty["type"],
  value: unknown,
): value is ScenarioValue {
  if (type === "integer")
    return typeof value === "number" && Number.isSafeInteger(value);
  if (type === "number")
    return typeof value === "number" && Number.isFinite(value);
  return typeof value === type;
}

/** Only code-owned, flat primitive schemas can produce controls. */
export function validateScenarioSchema(value: unknown): ScenarioSchema {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Scenario schema is unavailable.");
  const schema = value as Record<string, unknown>;
  const rootKeys = new Set([
    "type",
    "properties",
    "required",
    "additionalProperties",
    "title",
    "description",
  ]);
  if (
    schema.type !== "object" ||
    Object.keys(schema).some((key) => !rootKeys.has(key)) ||
    (schema.additionalProperties !== undefined &&
      schema.additionalProperties !== false)
  ) {
    throw new Error("This scenario uses unsupported controls.");
  }
  if (
    !schema.properties ||
    typeof schema.properties !== "object" ||
    Array.isArray(schema.properties)
  ) {
    throw new Error("Scenario fields are unavailable.");
  }
  const entries = Object.entries(schema.properties);
  if (!entries.length || entries.length > 40)
    throw new Error("Scenario field limit exceeded.");
  for (const [name, raw] of entries) {
    if (
      !/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(name) ||
      forbiddenNames.has(name) ||
      !raw ||
      typeof raw !== "object" ||
      Array.isArray(raw)
    )
      throw new Error("Invalid scenario field.");
    const property = raw as ScenarioProperty;
    if (
      !["string", "number", "integer", "boolean"].includes(property.type) ||
      Object.keys(raw).some((key) => !propertyKeys.has(key))
    )
      throw new Error("This scenario uses unsupported controls.");
    for (const key of ["title", "description", "unit"] as const) {
      if (property[key] !== undefined && typeof property[key] !== "string")
        throw new Error("Invalid scenario label.");
    }
    for (const key of [
      "minimum",
      "maximum",
      "exclusiveMinimum",
      "exclusiveMaximum",
      "minLength",
      "maxLength",
    ] as const) {
      const bound = property[key];
      if (
        bound !== undefined &&
        (typeof bound !== "number" || !Number.isFinite(bound))
      )
        throw new Error("Invalid scenario bounds.");
      if (
        (key === "minLength" || key === "maxLength") &&
        bound !== undefined &&
        (!Number.isInteger(bound) || bound < 0 || bound > 10000)
      )
        throw new Error("Invalid scenario text limit.");
    }
    const lower = property.exclusiveMinimum ?? property.minimum;
    const upper = property.exclusiveMaximum ?? property.maximum;
    if (
      lower !== undefined &&
      upper !== undefined &&
      (lower > upper ||
        (lower === upper &&
          (property.exclusiveMinimum !== undefined ||
            property.exclusiveMaximum !== undefined)))
    )
      throw new Error("Invalid scenario bounds.");
    if (
      property.minLength !== undefined &&
      property.maxLength !== undefined &&
      property.minLength > property.maxLength
    )
      throw new Error("Invalid scenario text limit.");
    if (
      property.enum !== undefined &&
      (!Array.isArray(property.enum) ||
        !property.enum.length ||
        property.enum.length > 100 ||
        property.enum.some((item) => !valueMatches(property.type, item)))
    )
      throw new Error("Invalid scenario choices.");
    if (
      property.default !== undefined &&
      !valueMatches(property.type, property.default)
    )
      throw new Error("Invalid scenario default.");
  }
  if (
    schema.required !== undefined &&
    (!Array.isArray(schema.required) ||
      schema.required.some(
        (key) =>
          typeof key !== "string" ||
          !Object.prototype.hasOwnProperty.call(schema.properties!, key),
      ))
  )
    throw new Error("Invalid required scenario fields.");
  return schema as unknown as ScenarioSchema;
}

export function scenarioDefaults(
  schema: ScenarioSchema,
): Record<string, string | boolean> {
  return Object.fromEntries(
    Object.entries(schema.properties).map(([key, field]) => [
      key,
      field.type === "boolean"
        ? field.default === true
        : field.default === undefined
          ? ""
          : String(field.default),
    ]),
  );
}

export function scenarioParameters(
  schema: ScenarioSchema,
  values: Record<string, string | boolean>,
): Record<string, ScenarioValue> {
  const result: Record<string, ScenarioValue> = {};
  for (const [key, property] of Object.entries(schema.properties)) {
    const raw = values[key];
    const label = property.title ?? key.replace(/_/g, " ");
    if (raw === undefined || raw === "") {
      if (schema.required?.includes(key))
        throw new Error(`${label} is required.`);
      continue;
    }
    const value =
      property.type === "number" || property.type === "integer"
        ? typeof raw === "string" && raw.trim()
          ? Number(raw)
          : NaN
        : raw;
    if (!valueMatches(property.type, value))
      throw new Error(`${label} has an invalid value.`);
    if (property.enum && !property.enum.includes(value))
      throw new Error(`${label} must use a registered choice.`);
    if (
      typeof value === "number" &&
      ((property.minimum !== undefined && value < property.minimum) ||
        (property.maximum !== undefined && value > property.maximum) ||
        (property.exclusiveMinimum !== undefined &&
          value <= property.exclusiveMinimum) ||
        (property.exclusiveMaximum !== undefined &&
          value >= property.exclusiveMaximum))
    )
      throw new Error(`${label} is outside the allowed range.`);
    if (
      typeof value === "string" &&
      (value.length < (property.minLength ?? 0) ||
        value.length > (property.maxLength ?? 10000))
    )
      throw new Error(`${label} has an invalid length.`);
    result[key] = value;
  }
  return result;
}

export const legacyUnitEconomics: ScenarioDefinition = {
  id: "unit-economics-v1",
  scenario_kind: "unit-economics",
  version: 1,
  title: "Unit economics",
  description:
    "Conditional revenue estimates based on unit and cost assumptions.",
  input_schema: {
    type: "object",
    additionalProperties: false,
    required: [
      "action_type",
      "baseline_units",
      "price",
      "unit_cost",
      "expected_unit_change",
      "unit_change_uncertainty",
      "action_cost",
      "capacity",
      "max_budget",
    ],
    properties: {
      baseline_units: { type: "number", title: "Baseline units", minimum: 0 },
      price: { type: "number", title: "Unit price", minimum: 0 },
      unit_cost: { type: "number", title: "Unit cost", minimum: 0 },
      capacity: {
        type: "number",
        title: "Capacity in the outcome period",
        minimum: 0,
      },
      max_budget: {
        type: "number",
        title: "Maximum action budget",
        minimum: 0,
      },
      action_type: {
        type: "string",
        title: "Action to simulate",
        default: "inventory_transfer",
        enum: [
          "inventory_transfer",
          "campaign_budget",
          "rollback",
          "discount",
          "spend",
        ],
      },
      expected_unit_change: {
        type: "number",
        title: "Expected change in units",
      },
      unit_change_uncertainty: {
        type: "number",
        title: "Unit sensitivity, ±",
        minimum: 0,
      },
      action_cost: { type: "number", title: "Action cost", minimum: 0 },
      discount: {
        type: "number",
        title: "Discount fraction",
        description: "Enter a fraction from 0 up to, but excluding, 1.",
        minimum: 0,
        exclusiveMaximum: 1,
        default: 0,
      },
    },
  },
  shared_input_schema: { type: "object", properties: {} },
};

const sharedFields = [
  "baseline_units",
  "price",
  "unit_cost",
  "capacity",
  "max_budget",
];
legacyUnitEconomics.shared_input_schema = {
  type: "object",
  additionalProperties: false,
  required: sharedFields,
  properties: Object.fromEntries(
    Object.entries(legacyUnitEconomics.input_schema.properties).filter(
      ([name]) => sharedFields.includes(name),
    ),
  ),
};
legacyUnitEconomics.input_schema = {
  ...legacyUnitEconomics.input_schema,
  required: legacyUnitEconomics.input_schema.required?.filter(
    (name) => !sharedFields.includes(name),
  ),
  properties: Object.fromEntries(
    Object.entries(legacyUnitEconomics.input_schema.properties).filter(
      ([name]) => !sharedFields.includes(name),
    ),
  ),
};
