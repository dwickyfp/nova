import { ApiError } from "@/lib/api-client";
import type { QueryResponse } from "./types";

/**
 * The messages a failed statement shows. The API repeats a statement's error in
 * its warnings, so each distinct message appears once.
 */
export function queryErrorMessages(
  result: Pick<QueryResponse, "error" | "warnings">,
): string[] {
  const messages = [result.error, ...(result.warnings ?? [])].filter(
    (message): message is string => Boolean(message?.trim()),
  );
  const unique = [...new Set(messages)];
  return unique.length ? unique : ["Query failed."];
}

/**
 * A refused statement (for example `INTO OUTFILE`) answers with a non-2xx status
 * whose body is still the list of statement results. Returns those results so
 * the worksheet can show why it was refused, or null for any other failure.
 */
export function refusedQueryResults(error: unknown): QueryResponse[] | null {
  if (!(error instanceof ApiError) || !Array.isArray(error.body)) return null;
  const results = error.body.filter(
    (item): item is QueryResponse =>
      Boolean(item) &&
      typeof item === "object" &&
      (item as { success?: unknown }).success === false &&
      typeof (item as { original_sql?: unknown }).original_sql === "string",
  );
  return results.length && results.length === error.body.length
    ? results
    : null;
}
