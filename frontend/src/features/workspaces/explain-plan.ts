import type { QueryResponse } from "./types";

// EXPLAIN ANALYZE output carries terminal color codes; the plan view shows text.
const ANSI_COLOR = new RegExp(`${String.fromCharCode(27)}\\[[0-9;]*m`, "g");

/**
 * Returns the plan text when a result set is an EXPLAIN plan, otherwise null.
 *
 * The check is on the leading keyword of the statement rather than on the
 * result shape: an EXPLAIN row set happens to hold one column today, but that
 * is an engine detail, while "the user asked for a plan" is the actual intent
 * we render for. The column-shape branch is kept only as a fallback for plans
 * loaded from history, where `original_sql` may have been rewritten.
 */
export function explainPlanFromResult(result: QueryResponse): string | null {
  if (!result.success || !result.rows.length) return null;
  const firstKeyword = result.original_sql
    .trim()
    .split(/\s+/, 1)[0]
    ?.toUpperCase();
  const looksLikePlan =
    firstKeyword === "EXPLAIN" ||
    (result.columns.length === 1 && /explain/i.test(result.columns[0] ?? ""));
  if (!looksLikePlan) return null;
  return result.rows
    .map((row) => String(row[0] ?? "").replace(ANSI_COLOR, ""))
    .join("\n");
}
