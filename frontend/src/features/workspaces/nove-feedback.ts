import type { QueryResponse } from "./types";

const MAX_SQL_CHARS = 4_000;
const MAX_ERROR_CHARS = 1_000;
const MAX_RESULT_COLUMNS = 32;
const SECRET_MARKER =
  /\b(?:password|passwd|secret|token|credential|api[_-]?key|private[_-]?key|identified\s+by)\b/i;

export type WorkspaceExecutionFeedback = {
  execution: {
    type: "sql";
    executionId: string;
    status: "success" | "error";
    errorMessage?: string;
    elapsedMs: number;
    rowCount: number;
    resultSchema: Array<{ name: string }>;
  };
  eventType: "query_completed" | "query_failed";
  eventPayload: Record<string, string | number | boolean>;
};

export function safeNoveSql(sql: string): string | undefined {
  const trimmed = sql.trim();
  if (
    !trimmed ||
    trimmed.length > MAX_SQL_CHARS ||
    SECRET_MARKER.test(trimmed)
  ) {
    return undefined;
  }
  return trimmed;
}

/** Replace only the statement still occupying the attachment's recorded lines. */
export function replaceAttachedSql(
  document: string,
  attachedSql: string,
  proposedSql: string,
  startLine: number,
  endLine: number,
): string | null {
  const original = attachedSql.trim();
  if (
    !original ||
    !proposedSql.trim() ||
    !Number.isInteger(startLine) ||
    !Number.isInteger(endLine) ||
    startLine < 1 ||
    endLine < startLine ||
    original.split("\n").length !== endLine - startLine + 1
  )
    return null;
  let matchIndex = -1;
  for (
    let index = document.indexOf(original);
    index !== -1;
    index = document.indexOf(original, index + 1)
  ) {
    const line = document.slice(0, index).split("\n").length;
    if (line !== startLine) continue;
    if (matchIndex !== -1) return null;
    matchIndex = index;
  }
  if (matchIndex < 0) return null;
  return (
    document.slice(0, matchIndex) +
    proposedSql.trim() +
    document.slice(matchIndex + original.length)
  );
}

function safeErrorMessage(message: string): string {
  const trimmed = message.trim();
  return SECRET_MARKER.test(trimmed)
    ? "Query failed. The error contains sensitive text and was omitted."
    : trimmed.slice(0, MAX_ERROR_CHARS);
}

/** Return only the execution evidence Nove needs; result rows stay in the workspace. */
export function summarizeQueryForNove(
  executionId: string,
  sql: string,
  results: QueryResponse[],
  elapsedMs: number,
): WorkspaceExecutionFeedback {
  const failed = results.find((result) => !result.success);
  const noResult = results.length === 0;
  const last = failed ?? results[results.length - 1];
  const errorMessage =
    failed || noResult
      ? safeErrorMessage(
          failed?.error ||
            failed?.warnings?.[0] ||
            "Query returned no execution result",
        )
      : undefined;
  const rowCount = results.reduce(
    (total, result) => total + (result.success ? result.row_count : 0),
    0,
  );
  const execution: WorkspaceExecutionFeedback["execution"] = {
    type: "sql",
    executionId,
    status: failed || noResult ? "error" : "success",
    ...(errorMessage ? { errorMessage } : {}),
    elapsedMs,
    rowCount,
    resultSchema: (last?.columns ?? [])
      .slice(0, MAX_RESULT_COLUMNS)
      .map((name) => ({ name: name.slice(0, 128) })),
  };
  const safeSql = failed || noResult ? safeNoveSql(sql) : undefined;
  return {
    execution,
    eventType: failed || noResult ? "query_failed" : "query_completed",
    eventPayload: {
      executionId,
      ...(failed || noResult
        ? safeSql
          ? { sql: safeSql }
          : { sqlOmitted: true }
        : {}),
      ...(errorMessage ? { errorMessage } : {}),
      elapsedMs,
      rowCount,
    },
  };
}
