import { describe, expect, it } from "vitest";
import {
  replaceAttachedSql,
  safeNoveSql,
  summarizeQueryForNove,
} from "./nove-feedback";
import type { QueryResponse } from "./types";

function result(overrides: Partial<QueryResponse> = {}): QueryResponse {
  return {
    success: true,
    columns: ["order_id"],
    rows: [[17]],
    row_count: 1,
    affected_rows: 0,
    elapsed_ms: 12,
    original_sql: "SELECT order_id FROM orders",
    executed_sql: "SELECT order_id FROM orders",
    warnings: [],
    ...overrides,
  };
}

describe("workspace feedback for Nove", () => {
  it("replaces only the attached statement in a multi-query worksheet", () => {
    const document = "SELECT 1;\nSELECT broken FROM orders;\nSELECT 3;";
    const next = replaceAttachedSql(
      document,
      "SELECT broken FROM orders;",
      "SELECT order_id FROM orders;",
      2,
      2,
    );
    expect(next).toBe("SELECT 1;\nSELECT order_id FROM orders;\nSELECT 3;");
    expect(
      replaceAttachedSql(document, "SELECT stale;", "SELECT fixed;", 2, 2),
    ).toBeNull();
  });

  it("uses the recorded line when identical statements occur elsewhere", () => {
    const document = "SELECT broken;\nSELECT broken;";
    expect(
      replaceAttachedSql(document, "SELECT broken;", "SELECT fixed;", 2, 2),
    ).toBe("SELECT broken;\nSELECT fixed;");
  });

  it("rejects a changed or moved attachment instead of rewriting its duplicate", () => {
    expect(
      replaceAttachedSql(
        "SELECT broken;\nSELECT edited;",
        "SELECT broken;",
        "SELECT fixed;",
        2,
        2,
      ),
    ).toBeNull();
    expect(
      replaceAttachedSql(
        "SELECT 1;\nSELECT 2;\nSELECT broken;",
        "SELECT broken;",
        "SELECT fixed;",
        2,
        2,
      ),
    ).toBeNull();
  });

  it("rejects an ambiguous match on the recorded line or an invalid range", () => {
    expect(
      replaceAttachedSql(
        "SELECT broken; SELECT broken;",
        "SELECT broken;",
        "SELECT fixed;",
        1,
        1,
      ),
    ).toBeNull();
    expect(
      replaceAttachedSql(
        "SELECT broken;\nFROM orders;",
        "SELECT broken;\nFROM orders;",
        "SELECT fixed FROM orders;",
        1,
        1,
      ),
    ).toBeNull();
  });

  it("keeps a failed query and its error available for repair without copying rows", () => {
    const feedback = summarizeQueryForNove(
      "run-1",
      "SELECT order_id FROM missing",
      [
        result({
          success: false,
          rows: [],
          row_count: 0,
          error: "Unknown table missing",
        }),
      ],
      30,
    );
    expect(feedback.execution.status).toBe("error");
    expect(feedback.execution.errorMessage).toBe("Unknown table missing");
    expect(feedback.eventType).toBe("query_failed");
    expect(feedback.eventPayload.sql).toBe("SELECT order_id FROM missing");
    expect(JSON.stringify(feedback)).not.toContain("rows");
  });

  it("requires every statement to succeed before the run is successful", () => {
    const feedback = summarizeQueryForNove(
      "run-2",
      "SELECT 1; SELECT * FROM missing",
      [result(), result({ success: false, error: "Unknown table" })],
      50,
    );
    expect(feedback.eventType).toBe("query_failed");
    expect(feedback.execution.rowCount).toBe(1);
  });

  it("reports bounded successful result metadata without result values", () => {
    const feedback = summarizeQueryForNove(
      "run-3",
      "SELECT order_id FROM orders",
      [result({ columns: Array.from({ length: 40 }, (_, i) => `c${i}`) })],
      12,
    );
    expect(feedback.eventType).toBe("query_completed");
    expect(feedback.execution.resultSchema).toHaveLength(32);
    expect(feedback.eventPayload).not.toHaveProperty("rows");
    expect(feedback.eventPayload).not.toHaveProperty("sql");
  });

  it("does not verify success when the API returns no statement outcome", () => {
    const feedback = summarizeQueryForNove("run-empty", "SELECT 1", [], 2);
    expect(feedback.eventType).toBe("query_failed");
    expect(feedback.execution.status).toBe("error");
  });

  it("omits credential-shaped or oversized SQL from events", () => {
    expect(
      safeNoveSql("CREATE USER a IDENTIFIED BY 'top secret'"),
    ).toBeUndefined();
    expect(safeNoveSql("SELECT " + "x".repeat(4_001))).toBeUndefined();
    const feedback = summarizeQueryForNove(
      "run-4",
      "SELECT token FROM accounts",
      [result({ success: false, error: "bad token abc123" })],
      10,
    );
    expect(feedback.eventPayload).not.toHaveProperty("sql");
    expect(feedback.eventPayload.errorMessage).not.toContain("abc123");
  });
});
