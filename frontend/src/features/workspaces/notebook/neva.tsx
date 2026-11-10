import { useEffect, useMemo, useRef } from "react";
import { z } from "zod";
import { defineNevaCapability, useNevaSurface } from "@/features/assistant";
import { buildRewriteHunks, type ProposedRewrite } from "@/features/assistant/query-attach";
import type { CellKind, NotebookCell, SqlEngine } from "./types";
import type { NotebookModel } from "./use-notebook";

/** A cell Neva proposed, waiting for the person to accept or dismiss it. */
export type CellProposal = {
  id: string;
  /** The cell to replace, or the one to insert below. */
  targetCellId: string;
  mode: "replace" | "insert_below";
  kind: CellKind;
  engine: SqlEngine | null;
  source: string;
  rewrite: ProposedRewrite;
};

const STATUS: Record<string, "idle" | "running" | "success" | "error"> = {
  queued: "running",
  running: "running",
  succeeded: "success",
  failed: "error",
  timeout: "error",
  lost: "error",
  cancelled: "idle",
  skipped: "idle",
};

const LANGUAGE: Record<CellKind, string> = { python: "python", sql: "sql", markdown: "markdown" };

function proposalFor(
  cells: NotebookCell[],
  args: { cell_id: string; kind: CellKind; engine: string; source: string; mode: "replace" | "insert_below" },
): CellProposal {
  const target = cells.find((cell) => cell.id === args.cell_id) ?? cells[cells.length - 1];
  if (!target && args.mode === "replace") throw new Error("That cell is not in this notebook.");
  const before = args.mode === "replace" ? (target?.source ?? "") : "";
  if (args.mode === "replace" && before === args.source && target?.kind === args.kind) {
    throw new Error("The cell already has this code.");
  }
  const id = `neva-cell-${crypto.randomUUID()}`;
  return {
    id,
    targetCellId: target?.id ?? "",
    mode: args.mode,
    kind: args.kind,
    engine: args.kind === "sql" ? (args.engine === "nova" ? "nova" : "duckdb") : null,
    source: args.source,
    rewrite: {
      attachmentId: id,
      tabId: target?.id ?? "",
      sourceContent: before,
      sql: args.source,
      hunks: buildRewriteHunks(before, args.source),
      sourceMessageId: id,
    },
  };
}

/**
 * Registers the open notebook with Neva: what it holds, which cell is
 * selected, the runtime and the last result, plus the two page actions Neva
 * may take — propose a cell (the person reviews it) and move to a cell.
 * Rendered only once the notebook loaded, so it is the newest surface and
 * takes over from the Workspace's SQL surface while the notebook is open.
 */
export function NotebookNevaSurface({
  notebookId,
  title,
  model,
  activeId,
  onPropose,
  onFocusCell,
  registerAsk,
}: {
  notebookId: string;
  title: string;
  model: NotebookModel;
  activeId: string | null;
  onPropose: (proposal: CellProposal) => void;
  onFocusCell: (cellId: string) => void;
  registerAsk: (ask: (prompt: string) => void) => void;
}) {
  const modelRef = useRef(model);
  modelRef.current = model;
  const capabilities = useMemo(
    () => [
      defineNevaCapability({
        name: "notebook.propose_cell",
        risk: "draft",
        argsSchema: z
          .object({
            cell_id: z.string().min(1).max(64),
            kind: z.enum(["python", "sql", "markdown"]),
            engine: z.enum(["duckdb", "nova", "none"]),
            source: z.string().min(1).max(20_000),
            mode: z.enum(["replace", "insert_below"]),
          })
          .strict(),
        execute: (args) => onPropose(proposalFor(modelRef.current.cells, args)),
      }),
      defineNevaCapability({
        name: "notebook.focus_cell",
        risk: "safe",
        argsSchema: z.object({ cell_id: z.string().min(1).max(64) }).strict(),
        execute: ({ cell_id }) => {
          if (!modelRef.current.cells.some((cell) => cell.id === cell_id)) {
            throw new Error("That cell is not in this notebook.");
          }
          onFocusCell(cell_id);
        },
      }),
    ],
    [onFocusCell, onPropose],
  );

  const active = model.cells.find((cell) => cell.id === activeId) ?? null;
  const activeRun = active ? model.runs[active.id] : undefined;
  const failedCells = model.cells.filter((cell) => model.runs[cell.id]?.status === "failed");

  const { publishEvent, askNeva } = useNevaSurface({
    id: "workspace.notebook",
    route: "/workspaces",
    title,
    context: () => ({
      entity: {
        type: "notebook",
        id: notebookId,
        name: title,
        metadata: {
          cells: model.cells.length,
          failedCells: failedCells.map((cell) => cell.id).slice(0, 10),
        },
      },
      ...(active
        ? {
            selection: { type: "cell", ids: [active.id] },
            editor: {
              documentId: active.id,
              language: LANGUAGE[active.kind],
              text: active.source.slice(0, 4000),
              dirty: model.saveState !== "saved",
            },
          }
        : {}),
      ...(activeRun
        ? {
            execution: {
              type: "notebook_cell",
              executionId: activeRun.executionId,
              status: STATUS[activeRun.status] ?? "idle",
              errorMessage: activeRun.message?.slice(0, 500) ?? null,
              elapsedMs: activeRun.durationMs,
            },
          }
        : {}),
      view: {
        summary: {
          runtime: model.runtime.status,
          novaDataRole: model.runtime.active_role ?? null,
          cellKind: active?.kind ?? null,
          sqlEngine: active?.kind === "sql" ? active.engine : null,
          resultName: active?.name ?? null,
        },
      },
      domain: { role: model.runtime.active_role ?? null },
    }),
    capabilities,
    suggestedActions: [
      ...(activeRun?.status === "failed"
        ? [{ label: "Fix this cell", prompt: "Explain why the selected cell failed and propose a fix." }]
        : []),
      { label: "Explain this notebook", prompt: "Explain what this notebook does, cell by cell." },
      {
        label: "Add a chart",
        prompt: "Add a Python cell below the selected one that charts its result.",
      },
    ],
  });

  // Each finished cell becomes an event Neva can refer to ("the last error").
  const published = useRef<Record<string, string>>({});
  useEffect(() => {
    for (const [cellId, run] of Object.entries(model.runs)) {
      const key = `${run.executionId}:${run.status}`;
      if (published.current[cellId] === key) continue;
      published.current[cellId] = key;
      if (run.status !== "succeeded" && run.status !== "failed") continue;
      publishEvent({
        source: "execution",
        type: run.status === "succeeded" ? "cell_completed" : "cell_failed",
        executionId: run.executionId,
        status: run.status === "succeeded" ? "success" : "failure",
        payload: { documentId: cellId, message: run.message?.slice(0, 300) ?? null },
      });
    }
  }, [model.runs, publishEvent]);

  useEffect(() => {
    registerAsk((prompt) => void askNeva(prompt));
  }, [askNeva, registerAsk]);

  return null;
}
