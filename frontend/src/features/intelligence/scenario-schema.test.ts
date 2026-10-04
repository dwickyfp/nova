import { describe, expect, it } from "vitest";
import {
  legacyUnitEconomics,
  scenarioDefaults,
  scenarioParameters,
  validateScenarioSchema,
} from "./scenario-schema";
import { capacityScenario } from "./scenario-fixtures.test-support";

describe("bounded registered scenario schemas", () => {
  it("supports a second reviewed adapter and empty shared fields", () => {
    const shared = validateScenarioSchema(capacityScenario.shared_input_schema);
    const option = validateScenarioSchema(capacityScenario.input_schema);
    expect({
      ...scenarioParameters(shared, { baseline: "20" }),
      ...scenarioParameters(option, {
        ...scenarioDefaults(option),
        additional_capacity: "10",
      }),
    }).toEqual({
      action_type: "capacity_upgrade",
      baseline: 20,
      additional_capacity: 10,
      action_cost: 0,
    });
    expect(
      scenarioParameters(
        validateScenarioSchema({
          type: "object",
          properties: {},
          additionalProperties: false,
        }),
        {},
      ),
    ).toEqual({});
  });
  it("preserves legacy unit-economics parameters including zero and fractional discount", () => {
    const shared = validateScenarioSchema(
      legacyUnitEconomics.shared_input_schema,
    );
    const option = validateScenarioSchema(legacyUnitEconomics.input_schema);
    expect({
      ...scenarioParameters(shared, {
        baseline_units: "10",
        price: "2",
        unit_cost: "0",
        capacity: "20",
        max_budget: "100",
      }),
      ...scenarioParameters(option, {
        ...scenarioDefaults(option),
        expected_unit_change: "-1",
        unit_change_uncertainty: "0",
        action_cost: "0",
        discount: "0.9999",
      }),
    }).toEqual({
      baseline_units: 10,
      price: 2,
      unit_cost: 0,
      capacity: 20,
      max_budget: 100,
      action_type: "inventory_transfer",
      expected_unit_change: -1,
      unit_change_uncertainty: 0,
      action_cost: 0,
      discount: 0.9999,
    });
  });
  it("rejects unknown, nested, reference, and executable schema controls", () => {
    for (const properties of [
      { payload: { type: "object", properties: {} } },
      { value: { $ref: "https://invalid.example/schema" } },
      { value: { type: "string", format: "html" } },
      { constructor: { type: "string" } },
    ])
      expect(() =>
        validateScenarioSchema({ type: "object", properties }),
      ).toThrow();
    expect(() =>
      validateScenarioSchema({
        type: "object",
        properties: { value: { type: "number" } },
        additionalProperties: true,
      }),
    ).toThrow();
  });
  it("fails on missing values, nonfinite numbers, exclusive limits and unregistered choices", () => {
    const option = validateScenarioSchema(legacyUnitEconomics.input_schema);
    const values = {
      ...scenarioDefaults(option),
      expected_unit_change: "1",
      unit_change_uncertainty: "0",
      action_cost: "0",
    };
    for (const discount of ["1", "Infinity", "-0.1"])
      expect(() =>
        scenarioParameters(option, { ...values, discount }),
      ).toThrow();
    expect(() =>
      scenarioParameters(option, { ...values, action_type: "unregistered" }),
    ).toThrow("registered choice");
    expect(() =>
      scenarioParameters(option, { ...values, action_cost: "" }),
    ).toThrow("required");
    expect(() =>
      scenarioParameters(option, { ...values, action_cost: "  " }),
    ).toThrow("invalid value");
  });
  it("supports optional fields, numeric enums, integers and explicit false without guessing", () => {
    const schema = validateScenarioSchema({
      type: "object",
      properties: {
        count: { type: "integer", minimum: 0, enum: [0, 2] },
        enabled: { type: "boolean" },
        note: { type: "string", minLength: 2 },
      },
      required: ["count", "enabled"],
    });
    expect(scenarioParameters(schema, { count: "0", enabled: false })).toEqual({
      count: 0,
      enabled: false,
    });
    expect(() =>
      scenarioParameters(schema, { count: "0.5", enabled: false }),
    ).toThrow();
    expect(() =>
      scenarioParameters(schema, { count: "2", enabled: false, note: "a" }),
    ).toThrow("length");
  });
  it("checks field count, malformed requirements, enum types and bounds before rendering", () => {
    expect(() =>
      validateScenarioSchema({
        type: "object",
        properties: { x: { type: "number", minimum: 3, maximum: 2 } },
      }),
    ).toThrow("bounds");
    expect(() =>
      validateScenarioSchema({
        type: "object",
        properties: { x: { type: "string", enum: [1] } },
      }),
    ).toThrow("choices");
    expect(() =>
      validateScenarioSchema({
        type: "object",
        properties: { x: { type: "string" } },
        required: ["missing"],
      }),
    ).toThrow();
    expect(() =>
      validateScenarioSchema({
        type: "object",
        properties: Object.fromEntries(
          Array.from({ length: 41 }, (_, i) => [
            `field${i}`,
            { type: "string" },
          ]),
        ),
      }),
    ).toThrow("limit");
  });
});
