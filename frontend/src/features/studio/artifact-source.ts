import { format as formatSql } from "sql-formatter";
import type { OrderedContent, TranscriptTurn } from "./studio-chat";

export type SavableContent = Extract<
  OrderedContent,
  { type: "table" | "chart" }
>;

/** Find the latest recorded SQL that produced a result block. */
export function findArtifactSql(
  turns: TranscriptTurn[],
  turnId: string,
  item: SavableContent,
): string | null {
  const turnIndex = turns.findIndex((turn) => turn.id === turnId);
  if (turnIndex < 0) return null;

  const toolCallId = item.block.tool_call_id;
  if (toolCallId) {
    const exact = turns[turnIndex].steps.find(
      (step) => step.id === toolCallId && isReusableSql(step.preview),
    );
    if (exact?.preview) return exact.preview.trim();
  }

  for (let index = turnIndex; index >= 0; index -= 1) {
    const steps = turns[index].steps;
    const producingStep =
      index === turnIndex && toolCallId
        ? steps.findIndex((step) => step.id === toolCallId)
        : -1;
    const lastCandidate =
      producingStep >= 0 ? producingStep - 1 : steps.length - 1;
    for (let stepIndex = lastCandidate; stepIndex >= 0; stepIndex -= 1) {
      const step = steps[stepIndex];
      if (
        step.kind === "tool" &&
        (step.label === "semantic_query" || step.label === "query_execute") &&
        isReusableSql(step.preview)
      ) {
        return step.preview!.trim();
      }
    }
  }
  return null;
}

export function isReusableSql(value?: string): boolean {
  if (!value) return false;
  const sql = value.trim().replace(/^(?:--[^\n]*\n|\/\*[\s\S]*?\*\/\s*)+/, "");
  return /^(?:SELECT|WITH|SHOW|DESC(?:RIBE)?|EXPLAIN)\b/i.test(sql);
}

/** Format stored SQL for reading without ever mutating the persisted query. */
export function formatArtifactSql(sql: string): string {
  try {
    return formatSql(sql, {
      language: "mysql",
      keywordCase: "upper",
      tabWidth: 2,
    });
  } catch {
    return sql;
  }
}
