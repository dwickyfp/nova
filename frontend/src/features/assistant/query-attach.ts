/**
 * Query attachments ("Attach to Nova") and the rewrite diff model.
 *
 * A workspace selection is attached to the assistant composer as a badge and
 * sent alongside the next message as context. When the assistant proposes a
 * rewritten query, the workspace renders the change as inline Monaco
 * decorations: the replaced lines in red, the proposed lines in blue, with an
 * Approve/Deny affordance.
 */

export type AttachedQuery = {
  /** Stable id so the composer can key the badge and remove one attachment. */
  id: string;
  /** The exact selected SQL text. */
  sql: string;
  /** Workspace tab the selection came from. */
  tabId: string;
  /** Human label for the badge, typically the file name. */
  fileName: string;
  database: string | null;
  schema: string | null;
  role: string | null;
  /** 1-based inclusive range in the source tab, used to locate the rewrite. */
  startLine: number;
  endLine: number;
  createdAt: number;
};

export type RewriteKind = "replace" | "insert" | "delete";

/**
 * A single line-range change in the editor. `startLine`/`endLine` are 1-based
 * inclusive and refer to the *original* document. For `replace`/`delete` they
 * are the lines being replaced; for `insert` they are the anchor line the new
 * lines go *after* (use `startLine === 0` to insert at the top).
 * `lines` is the proposed replacement, empty for a deletion.
 */
export type RewriteHunk = {
  kind: RewriteKind;
  startLine: number;
  endLine: number;
  lines: string[];
};

export type ProposedRewrite = {
  /** Attachment the rewrite answers, so stale proposals can be discarded. */
  attachmentId: string;
  tabId: string;
  /** Full proposed SQL document after applying every hunk. */
  sql: string;
  hunks: RewriteHunk[];
  /** The assistant message that produced it, for tracing. */
  sourceMessageId: string;
};

export type AttachContext = Omit<AttachedQuery, "id" | "createdAt">;

let attachmentSeq = 0;
export function nextAttachmentId() {
  attachmentSeq += 1;
  return `att-${Date.now().toString(36)}-${attachmentSeq}`;
}

/**
 * Serializes attachments into a prompt preamble prepended to the user's
 * message, so the model sees the exact SQL plus the worksheet it came from.
 * The preamble is plain text, not markdown, to avoid the model echoing it.
 */
export function formatAttachmentsForPrompt(
  attachments: AttachedQuery[],
): string {
  if (attachments.length === 0) return "";
  const blocks = attachments.map((attachment, index) => {
    const where = [
      attachment.fileName ? `file: ${attachment.fileName}` : null,
      attachment.database ? `database: ${attachment.database}` : null,
      attachment.schema ? `schema: ${attachment.schema}` : null,
      attachment.role ? `role: ${attachment.role}` : null,
    ]
      .filter(Boolean)
      .join(", ");
    const label =
      attachments.length > 1 ? `Attached query ${index + 1}` : "Attached query";
    return `[${label}${where ? ` (${where})` : ""}]\n\`\`\`sql\n${attachment.sql}\n\`\`\``;
  });
  return `${blocks.join("\n\n")}\n\n---\n\n`;
}

type DiffOp = { type: "equal" | "delete" | "insert"; line: string };

/**
 * Line-level LCS diff between two documents. Operates on right-trimmed lines so
 * a trailing-whitespace change does not count as an edit, then emits one op per
 * line. Equal runs are emitted so callers can map lines back to their position.
 */
export function diffLines(before: string[], after: string[]): DiffOp[] {
  const a = before.map((line) => line.trimEnd());
  const b = after.map((line) => line.trimEnd());
  const n = a.length;
  const m = b.length;
  // lcs[i][j] = length of the LCS of a[i:] and b[j:].
  const lcs: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      lcs[i][j] =
        a[i] === b[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const ops: DiffOp[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ type: "equal", line: before[i] });
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      ops.push({ type: "delete", line: before[i] });
      i += 1;
    } else {
      ops.push({ type: "insert", line: after[j] });
      j += 1;
    }
  }
  while (i < n) {
    ops.push({ type: "delete", line: before[i] });
    i += 1;
  }
  while (j < m) {
    ops.push({ type: "insert", line: after[j] });
    j += 1;
  }
  return ops;
}

/**
 * Turns a full before/after pair into editor hunks. Adjacent delete+insert runs
 * collapse into a single `replace` so a rewritten line is one hunk, not two.
 * A pure insert is anchored on the line it follows; a pure delete has no
 * proposed lines. Equal runs advance the original line counter.
 */
export function buildRewriteHunks(
  before: string,
  after: string,
): RewriteHunk[] {
  const ops = diffLines(before.split("\n"), after.split("\n"));
  const hunks: RewriteHunk[] = [];

  let lineNo = 1;
  let index = 0;
  while (index < ops.length) {
    const op = ops[index];
    if (op.type === "equal") {
      lineNo += 1;
      index += 1;
      continue;
    }

    const rangeStart = lineNo;
    const deleted: string[] = [];
    const inserted: string[] = [];
    while (index < ops.length && ops[index].type !== "equal") {
      const current = ops[index];
      if (current.type === "delete") {
        deleted.push(current.line);
        lineNo += 1;
      } else {
        inserted.push(current.line);
      }
      index += 1;
    }

    if (deleted.length === 0) {
      hunks.push({
        kind: "insert",
        startLine: rangeStart - 1,
        endLine: rangeStart - 1,
        lines: inserted,
      });
    } else {
      const endLine = lineNo - 1;
      const kind: RewriteKind = inserted.length === 0 ? "delete" : "replace";
      hunks.push({ kind, startLine: rangeStart, endLine, lines: inserted });
    }
  }

  return hunks;
}

/**
 * Applies the hunks to the original document, producing the proposed text.
 * Kept separate from `buildRewriteHunks` so the editor can apply a subset of
 * hunks (a later per-hunk approve) without recomputing the diff. Hunks are
 * applied bottom-up so earlier line numbers stay valid.
 */
export function applyHunks(before: string, hunks: RewriteHunk[]): string {
  const lines = before.split("\n");
  const ordered = [...hunks].sort((a, b) => b.startLine - a.startLine);
  for (const hunk of ordered) {
    if (hunk.kind === "insert") {
      lines.splice(hunk.startLine, 0, ...hunk.lines);
      continue;
    }
    const startIndex = hunk.startLine - 1;
    const deleteCount = hunk.endLine - hunk.startLine + 1;
    lines.splice(startIndex, deleteCount, ...hunk.lines);
  }
  return lines.join("\n");
}

/**
 * A fenced ```sql block is how the assistant is expected to answer a rewrite
 * request. We take the last complete block: a model often narrates first and
 * puts the final query last, and picking the first could capture an example.
 */
export function extractSqlCodeBlock(markdown: string): string | null {
  const pattern = /```(?:sql|mysql)\s*\n([\s\S]*?)```/gi;
  let match: RegExpExecArray | null;
  let last: string | null = null;
  while ((match = pattern.exec(markdown)) !== null) {
    const code = match[1]?.trim();
    if (code) last = code;
  }
  return last;
}
