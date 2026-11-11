import type { QueryResponse } from "./types";

/**
 * The role a `USE ROLE` / `SET ROLE` statement switched the session to.
 *
 * The server answers a role switch with one row of `CURRENT_ROLE()` and
 * `SECURITY_CONTEXT_VERSION` and no engine statement (`executed_sql` is empty),
 * so an ordinary query that happens to return those column names is not one.
 */
export function roleSwitchFromResults(
  results: QueryResponse[],
): { activeRole: string; securityContextVersion: number } | null {
  for (const result of [...results].reverse()) {
    if (
      result.success &&
      !result.executed_sql &&
      result.columns.length === 2 &&
      result.columns[0] === "CURRENT_ROLE()" &&
      result.columns[1] === "SECURITY_CONTEXT_VERSION" &&
      result.rows.length === 1
    ) {
      const [role, version] = result.rows[0];
      if (typeof role === "string" && role && typeof version === "number")
        return { activeRole: role, securityContextVersion: version };
    }
  }
  return null;
}
