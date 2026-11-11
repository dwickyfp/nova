import { describe, expect, it } from "vitest";
import { roleSwitchFromResults } from "./session-role";
import type { QueryResponse } from "./types";

function result(columns: string[], rows: unknown[][], executed_sql = "") {
  return { success: true, columns, rows, executed_sql, original_sql: "" } as unknown as QueryResponse;
}

describe("roleSwitchFromResults", () => {
  it("reads the role a USE ROLE switched to", () => {
    expect(
      roleSwitchFromResults([result(["CURRENT_ROLE()", "SECURITY_CONTEXT_VERSION"], [["analyst", 7]])]),
    ).toEqual({ activeRole: "analyst", securityContextVersion: 7 });
  });

  it("ignores a query that returns the same column names", () => {
    expect(
      roleSwitchFromResults([
        result(["CURRENT_ROLE()", "SECURITY_CONTEXT_VERSION"], [["analyst", 7]], "SELECT ..."),
      ]),
    ).toBeNull();
  });

  it("ignores ordinary results", () => {
    expect(roleSwitchFromResults([result(["CURRENT_ROLE()"], [["analyst"]], "SELECT CURRENT_ROLE()")])).toBeNull();
  });
});
