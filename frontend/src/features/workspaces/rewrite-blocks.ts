import type { ProposedRewrite } from "@/features/assistant/query-attach";

export type DiffLine = {
  kind: "removed" | "added" | "context";
  /** Line number in the current SQL; absent for an added line. */
  old?: number;
  /** Line number in the suggested SQL; absent for a removed line. */
  new?: number;
  text: string;
};

export type DiffBlock = { key: string; line: number; rows: DiffLine[] };

/**
 * Unified rows per change, as a code review shows them: one unchanged line
 * above, the lines it replaces, the lines it adds, one unchanged line below.
 */
export function rewriteBlocks(rewrite: ProposedRewrite): DiffBlock[] {
  const source = rewrite.sourceContent.split("\n");
  let offset = 0;
  return rewrite.hunks.map((hunk, index) => {
    const inserting = hunk.kind === "insert";
    // Last unchanged line before the change and first one after it (1-based).
    const before = inserting ? hunk.startLine : hunk.startLine - 1;
    const after = inserting ? hunk.startLine + 1 : hunk.endLine + 1;
    const removed: DiffLine[] = inserting
      ? []
      : source.slice(hunk.startLine - 1, hunk.endLine).map((text, line) => ({
          kind: "removed",
          old: hunk.startLine + line,
          text,
        }));
    const added: DiffLine[] = hunk.lines.map((text, line) => ({
      kind: "added",
      new: before + 1 + offset + line,
      text,
    }));
    const rows: DiffLine[] = [];
    if (before >= 1 && before <= source.length)
      rows.push({ kind: "context", old: before, new: before + offset, text: source[before - 1] });
    rows.push(...removed, ...added);
    offset += added.length - removed.length;
    if (after >= 1 && after <= source.length)
      rows.push({ kind: "context", old: after, new: after + offset, text: source[after - 1] });
    return {
      key: `${hunk.startLine}-${index}`,
      line: removed[0]?.old ?? added[0]?.new ?? Math.max(1, before),
      rows,
    };
  });
}
