import { describe, expect, it } from "vitest";
import { explainPlanFromResult } from "./explain-plan";
import type { QueryResponse } from "./types";

const ESC = String.fromCharCode(27);

function result(sql: string, rows: unknown[][], columns = ["Explain String"]) {
  return { success: true, original_sql: sql, columns, rows } as unknown as QueryResponse;
}

describe("explainPlanFromResult", () => {
  it("joins plan rows into text", () => {
    expect(
      explainPlanFromResult(result("EXPLAIN SELECT 1", [["PLAN FRAGMENT 0"], ["  RESULT SINK"]])),
    ).toBe("PLAN FRAGMENT 0\n  RESULT SINK");
  });

  it("drops the color codes EXPLAIN ANALYZE prints", () => {
    expect(
      explainPlanFromResult(
        result("EXPLAIN ANALYZE SELECT 1", [[`${ESC}[0mSummary${ESC}[0m`], [`    ${ESC}[1;31mQueryId: q${ESC}[0m`]]),
      ),
    ).toBe("Summary\n    QueryId: q");
  });

  it("ignores ordinary results", () => {
    expect(explainPlanFromResult(result("SELECT 1", [[1]], ["1"]))).toBeNull();
  });
});
