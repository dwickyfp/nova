import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api-client";
import { notebookApi } from "./api";
import { useNotebookEvents, type NotebookEvent } from "./stream";
import type {
  CellKind,
  CellOutput,
  ExecutionStatus,
  NotebookCell,
  NotebookEdit,
  RunMode,
  RuntimeInfo,
  RuntimeLimits,
  SqlEngine,
  VariableInfo,
} from "./types";

export type SaveState = "saved" | "saving" | "unsaved" | "conflict" | "error";

export type CellRun = {
  executionId: string;
  status: ExecutionStatus;
  executionCount: number | null;
  outputs: CellOutput[];
  durationMs: number | null;
  message: string | null;
  sourceRevision: string | null;
  generation: number | null;
  startedAt: number | null;
  /** Outputs were stored but are too large to load with the notebook. */
  outputsPending?: boolean;
  /** The runtime that produced the outputs. */
  runtimeId?: string | null;
};

type UndoStep = { label: string; edits: NotebookEdit[]; cells: NotebookCell[] };

const SOURCE_SAVE_DELAY = 600;
const RUNNING: ExecutionStatus[] = ["queued", "running"];

function newCellId() {
  return Math.random().toString(16).slice(2, 10) + Date.now().toString(16).slice(-8);
}

/** A cell's run is stale when it no longer shows what the cell would produce. */
export function isStale(
  cell: NotebookCell,
  run: CellRun | undefined,
  generation: number | null,
  movedStale: Set<string>,
  runtimeId: string | null = null,
): boolean {
  if (!run || RUNNING.includes(run.status) || run.status === "skipped") return false;
  if (run.sourceRevision && run.sourceRevision !== cell.source_revision) return true;
  // Outputs of an earlier runtime describe variables the live one no longer has.
  if (runtimeId && run.runtimeId && run.runtimeId !== runtimeId) return true;
  if (generation != null && run.generation != null && run.generation < generation) return true;
  return movedStale.has(cell.id);
}

export function useNotebook(notebookId: string) {
  const [cells, setCells] = useState<NotebookCell[]>([]);
  const [title, setTitle] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [runtime, setRuntime] = useState<RuntimeInfo>({ status: "ABSENT" });
  const [limits, setLimits] = useState<RuntimeLimits | null>(null);
  const [runs, setRuns] = useState<Record<string, CellRun>>({});
  const [variables, setVariables] = useState<VariableInfo[] | null>(null);
  const [movedStale, setMovedStale] = useState<Set<string>>(new Set());
  const undoStack = useRef<UndoStep[]>([]);
  const [undoDepth, setUndoDepth] = useState(0);
  const [packageEvents, setPackageEvents] = useState<NotebookEvent[]>([]);

  const revisionRef = useRef(0);
  const cellsRef = useRef<NotebookCell[]>([]);
  cellsRef.current = cells;
  const pendingSources = useRef<Map<string, string>>(new Map());
  const pendingEdits = useRef<NotebookEdit[]>([]);
  const saveTimer = useRef<number | null>(null);
  const saving = useRef<Promise<void>>(Promise.resolve());
  const conflictRef = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [doc, latest, status] = await Promise.all([
        notebookApi.get(notebookId),
        notebookApi.latest(notebookId).catch(() => null),
        notebookApi.runtime(notebookId).catch(() => null),
      ]);
      revisionRef.current = doc.notebook.revision;
      conflictRef.current = false;
      pendingSources.current.clear();
      pendingEdits.current = [];
      setCells(doc.notebook.cells);
      setTitle(doc.entry.name);
      setSaveState("saved");
      setMovedStale(new Set());
      if (status) {
        setRuntime(status.runtime);
        setLimits(status.limits);
      }
      if (latest) {
        const next: Record<string, CellRun> = {};
        for (const item of latest.executions) {
          next[item.cell_id] = {
            executionId: item.execution_id,
            status: item.status,
            executionCount: item.execution_count ?? null,
            outputs: item.outputs ?? [],
            outputsPending: item.outputs === null,
            durationMs: item.duration_ms ?? null,
            message: item.error_message ?? null,
            sourceRevision: item.source_revision ?? null,
            generation: item.generation ?? null,
            startedAt: null,
            runtimeId: item.runtime_id ?? null,
          };
        }
        for (const item of latest.inflight) {
          next[item.cell_id] = {
            executionId: item.execution_id,
            status: item.status,
            executionCount: item.execution_count ?? null,
            outputs: [],
            durationMs: null,
            message: null,
            sourceRevision: null,
            generation: latest.generation,
            startedAt: item.status === "running" ? Date.now() : null,
          };
        }
        setRuns(next);
        setVariables(latest.variables);
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "The notebook could not be opened");
    } finally {
      setLoading(false);
    }
  }, [notebookId]);

  useEffect(() => {
    void load();
  }, [load]);

  // ── Saving ────────────────────────────────────────────────────────────

  const flush = useCallback((): Promise<void> => {
    if (saveTimer.current) {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    saving.current = saving.current.then(async () => {
      if (conflictRef.current) return;
      const edits: NotebookEdit[] = [...pendingEdits.current];
      pendingEdits.current = [];
      for (const [cellId, source] of pendingSources.current) {
        edits.push({ op: "update", cell_id: cellId, source });
      }
      pendingSources.current.clear();
      if (!edits.length) return;
      setSaveState("saving");
      try {
        const response = await notebookApi.edit(notebookId, revisionRef.current, edits);
        revisionRef.current = response.notebook.revision;
        const revisions = new Map(
          response.notebook.cells.map((cell) => [cell.id, cell.source_revision]),
        );
        setCells((current) =>
          current.map((cell) => {
            const revision = revisions.get(cell.id);
            // Text typed after this save began keeps its own pending state.
            if (!revision || pendingSources.current.has(cell.id)) return cell;
            return { ...cell, source_revision: revision };
          }),
        );
        setSaveState(
          pendingSources.current.size || pendingEdits.current.length ? "unsaved" : "saved",
        );
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          conflictRef.current = true;
          setSaveState("conflict");
        } else {
          // Keep the edits for the next attempt: plain text edits go back to
          // the per-cell buffer (unless newer text arrived), the rest in order.
          const retry: NotebookEdit[] = [];
          for (const edit of edits) {
            const textOnly =
              edit.op === "update" &&
              edit.source !== undefined &&
              edit.kind === undefined &&
              edit.name === undefined &&
              edit.engine === undefined;
            if (textOnly) {
              if (!pendingSources.current.has(edit.cell_id)) {
                pendingSources.current.set(edit.cell_id, edit.source as string);
              }
            } else {
              retry.push(edit);
            }
          }
          pendingEdits.current = [...retry, ...pendingEdits.current];
          setSaveState("error");
        }
      }
    });
    return saving.current;
  }, [notebookId]);

  const scheduleSave = useCallback(() => {
    setSaveState((state) => (state === "conflict" ? state : "unsaved"));
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => void flush(), SOURCE_SAVE_DELAY);
  }, [flush]);

  const structural = useCallback(
    (edits: NotebookEdit[]) => {
      pendingEdits.current.push(...edits);
      void flush();
    },
    [flush],
  );

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (pendingSources.current.size || pendingEdits.current.length) {
        void flush();
        event.preventDefault();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      void flush();
    };
  }, [flush]);

  // ── Editing ───────────────────────────────────────────────────────────

  const pushUndo = useCallback((step: UndoStep) => {
    undoStack.current = [...undoStack.current.slice(-49), step];
    setUndoDepth(undoStack.current.length);
  }, []);

  const updateSource = useCallback(
    (cellId: string, source: string) => {
      setCells((current) =>
        current.map((cell) => (cell.id === cellId ? { ...cell, source } : cell)),
      );
      pendingSources.current.set(cellId, source);
      scheduleSave();
    },
    [scheduleSave],
  );

  const updateCell = useCallback(
    (cellId: string, patch: { name?: string | null; kind?: CellKind; engine?: SqlEngine }) => {
      setCells((current) =>
        current.map((cell) =>
          cell.id === cellId
            ? {
                ...cell,
                ...patch,
                name: patch.kind === "markdown" ? null : patch.name !== undefined ? patch.name : cell.name,
                engine: (patch.kind ?? cell.kind) === "sql" ? (patch.engine ?? cell.engine ?? "duckdb") : null,
              }
            : cell,
        ),
      );
      const pending = pendingSources.current.get(cellId);
      pendingSources.current.delete(cellId);
      structural([
        {
          op: "update",
          cell_id: cellId,
          ...patch,
          ...(pending !== undefined ? { source: pending } : {}),
        },
      ]);
    },
    [structural],
  );

  const insertCell = useCallback(
    (
      kind: CellKind,
      index: number,
      source = "",
      name: string | null = null,
      engine: SqlEngine = "duckdb",
    ): string => {
      const id = newCellId();
      const usedNames = new Set(cellsRef.current.map((c) => c.name).filter(Boolean));
      let cellName = name;
      if (kind === "sql" && !cellName) {
        let n = cellsRef.current.filter((c) => c.kind === "sql").length + 1;
        while (usedNames.has(`result_${n}`)) n += 1;
        cellName = `result_${n}`;
      }
      const cell: NotebookCell = {
        id,
        kind,
        source,
        name: cellName,
        engine: kind === "sql" ? engine : null,
        source_revision: "",
      };
      setCells((current) => {
        const next = [...current];
        next.splice(Math.max(0, Math.min(index, next.length)), 0, cell);
        return next;
      });
      structural([
        {
          op: "insert",
          index,
          cell: { id, kind, source, name: cellName, ...(kind === "sql" ? { engine } : {}) },
        },
      ]);
      pushUndo({ label: "Add cell", edits: [{ op: "delete", cell_id: id }], cells: [] });
      return id;
    },
    [pushUndo, structural],
  );

  const deleteCell = useCallback(
    (cellId: string) => {
      const index = cellsRef.current.findIndex((c) => c.id === cellId);
      if (index < 0) return;
      const removed = cellsRef.current[index];
      const pending = pendingSources.current.get(cellId);
      pendingSources.current.delete(cellId);
      const restored = pending !== undefined ? { ...removed, source: pending } : removed;
      setCells((current) => current.filter((c) => c.id !== cellId));
      structural([{ op: "delete", cell_id: cellId }]);
      pushUndo({
        label: "Delete cell",
        edits: [
          {
            op: "insert",
            index,
            cell: {
              id: restored.id,
              kind: restored.kind,
              source: restored.source,
              name: restored.name,
              ...(restored.engine ? { engine: restored.engine } : {}),
            },
          },
        ],
        cells: [restored],
      });
    },
    [pushUndo, structural],
  );

  const moveCell = useCallback(
    (cellId: string, toIndex: number) => {
      const from = cellsRef.current.findIndex((c) => c.id === cellId);
      if (from < 0 || from === toIndex) return;
      const run = runs[cellId];
      if (run && RUNNING.includes(run.status)) return;
      const next = [...cellsRef.current];
      const [cell] = next.splice(from, 1);
      const target = Math.max(0, Math.min(toIndex, next.length));
      next.splice(target, 0, cell);
      setCells(next);
      // Conservative: everything from the first moved position down may now
      // depend on a different order of Python state.
      const firstAffected = Math.min(from, target);
      setMovedStale((current) => {
        const updated = new Set(current);
        for (const c of next.slice(firstAffected)) {
          if (c.kind !== "markdown" && runs[c.id]) updated.add(c.id);
        }
        return updated;
      });
      structural([{ op: "move", cell_id: cellId, index: target }]);
      pushUndo({ label: "Move cell", edits: [{ op: "move", cell_id: cellId, index: from }], cells: [] });
    },
    [pushUndo, runs, structural],
  );

  const duplicateCell = useCallback(
    (cellId: string) => {
      const index = cellsRef.current.findIndex((c) => c.id === cellId);
      if (index < 0) return;
      const cell = cellsRef.current[index];
      return insertCell(cell.kind, index + 1, cell.source, null);
    },
    [insertCell],
  );

  // Reads the stack from a ref: an Undo button in a toast keeps the callback
  // of the render that created it.
  const undo = useCallback(() => {
    const step = undoStack.current[undoStack.current.length - 1];
    if (!step) return null;
    undoStack.current = undoStack.current.slice(0, -1);
    setUndoDepth(undoStack.current.length);
    setCells((current) => {
      let next = [...current];
      for (const edit of step.edits) {
        if (edit.op === "delete") next = next.filter((c) => c.id !== edit.cell_id);
        else if (edit.op === "insert") {
          const cell = step.cells.find((c) => c.id === edit.cell.id);
          if (cell) next.splice(edit.index, 0, cell);
        } else if (edit.op === "move") {
          const from = next.findIndex((c) => c.id === edit.cell_id);
          if (from >= 0) {
            const [cell] = next.splice(from, 1);
            next.splice(edit.index, 0, cell);
          }
        }
      }
      return next;
    });
    structural(step.edits);
    return step.label;
  }, [structural]);

  // ── Runtime and runs ──────────────────────────────────────────────────

  const run = useCallback(
    async (mode: RunMode, cellId?: string, onError: "stop" | "continue" = "stop") => {
      await flush();
      if (conflictRef.current) {
        throw new Error("The notebook changed in another tab. Reload it before running.");
      }
      const response = await notebookApi.run(notebookId, {
        mode,
        cell_id: cellId,
        base_revision: revisionRef.current,
        on_error: onError,
      });
      setRuns((current) => {
        const next = { ...current };
        for (const item of response.executions) {
          next[item.cell_id] = {
            ...(current[item.cell_id] ?? {
              outputs: [],
              durationMs: null,
              executionCount: null,
              message: null,
              generation: null,
              startedAt: null,
            }),
            executionId: item.execution_id,
            status: "queued",
            sourceRevision: item.source_revision,
          };
        }
        return next;
      });
      setMovedStale((current) => {
        const updated = new Set(current);
        for (const item of response.executions) updated.delete(item.cell_id);
        return updated;
      });
      return response;
    },
    [flush, notebookId],
  );

  const cancel = useCallback(
    (executionId?: string) => notebookApi.cancel(notebookId, executionId),
    [notebookId],
  );

  const startRuntime = useCallback(async () => {
    const response = await notebookApi.start(notebookId);
    setRuntime(response.runtime);
  }, [notebookId]);

  const stopRuntime = useCallback(async () => {
    await flush();
    const response = await notebookApi.stop(notebookId);
    setRuntime((current) => ({ ...current, ...response.runtime }));
  }, [flush, notebookId]);

  const restartRuntime = useCallback(async () => {
    const response = await notebookApi.restart(notebookId);
    setRuntime((current) => ({ ...current, ...response.runtime }));
  }, [notebookId]);

  const clearOutputs = useCallback((cellId?: string) => {
    setRuns((current) => {
      if (!cellId) {
        return Object.fromEntries(
          Object.entries(current).filter(([, run]) => RUNNING.includes(run.status)),
        );
      }
      const next = { ...current };
      if (next[cellId] && !RUNNING.includes(next[cellId].status)) delete next[cellId];
      return next;
    });
  }, []);

  // ── Events ────────────────────────────────────────────────────────────

  const onEvent = useCallback((event: NotebookEvent) => {
    const data = event.data as Record<string, unknown>;
    switch (event.event) {
      case "snapshot": {
        const snapshot = data as { runtime?: RuntimeInfo; limits?: RuntimeLimits };
        if (snapshot.runtime) setRuntime(snapshot.runtime);
        if (snapshot.limits) setLimits(snapshot.limits);
        break;
      }
      case "runtime.status":
        setRuntime((current) => ({ ...current, ...(data as RuntimeInfo) }));
        break;
      case "runtime.restarted":
        setRuntime((current) => ({
          ...current,
          generation: Number(data.generation ?? current.generation ?? 1),
        }));
        setVariables(null);
        break;
      case "cell.queued":
      case "cell.started": {
        const cellId = String(data.cell_id);
        setRuns((current) => ({
          ...current,
          [cellId]: {
            outputs: event.event === "cell.started" ? [] : (current[cellId]?.outputs ?? []),
            durationMs: null,
            message: null,
            generation: current[cellId]?.generation ?? null,
            executionCount:
              event.event === "cell.started"
                ? Number(data.execution_count ?? 0) || null
                : (current[cellId]?.executionCount ?? null),
            executionId: String(data.execution_id),
            status: event.event === "cell.started" ? "running" : "queued",
            sourceRevision: (data.source_revision as string) ?? current[cellId]?.sourceRevision ?? null,
            startedAt: event.event === "cell.started" ? Date.now() : null,
          },
        }));
        break;
      }
      case "cell.output": {
        const cellId = String(data.cell_id);
        const output = data.output as CellOutput & { _append?: boolean };
        setRuns((current) => {
          const existing = current[cellId];
          if (!existing || existing.executionId !== data.execution_id) return current;
          const outputs = [...existing.outputs];
          const last = outputs[outputs.length - 1];
          if (
            output.type === "stream" &&
            last?.type === "stream" &&
            last.name === output.name
          ) {
            outputs[outputs.length - 1] = { ...last, text: last.text + output.text };
          } else {
            const { _append: _ignored, ...clean } = output;
            outputs.push(clean as CellOutput);
          }
          return { ...current, [cellId]: { ...existing, outputs } };
        });
        break;
      }
      case "cell.finished": {
        const cellId = String(data.cell_id);
        setRuns((current) => {
          const existing = current[cellId];
          if (existing && existing.executionId !== data.execution_id) return current;
          return {
            ...current,
            [cellId]: {
              executionId: String(data.execution_id),
              status: data.status as ExecutionStatus,
              executionCount:
                (data.execution_count as number | undefined) ?? existing?.executionCount ?? null,
              outputs: (data.outputs as CellOutput[] | undefined) ?? existing?.outputs ?? [],
              durationMs: (data.duration_ms as number | undefined) ?? null,
              message: (data.message as string | null | undefined) ?? null,
              sourceRevision:
                (data.source_revision as string | undefined) ?? existing?.sourceRevision ?? null,
              generation: (data.generation as number | undefined) ?? existing?.generation ?? null,
              startedAt: existing?.startedAt ?? null,
              runtimeId: (data.runtime_id as string | undefined) ?? existing?.runtimeId ?? null,
            },
          };
        });
        if (Array.isArray(data.variables)) setVariables(data.variables as VariableInfo[]);
        break;
      }
      case "package.log":
      case "package.finished":
        setPackageEvents((current) => [...current.slice(-199), event]);
        break;
      default:
        break;
    }
  }, []);

  useNotebookEvents(notebookId, onEvent);

  const generation = runtime.generation ?? null;
  const liveRuntimeId =
    runtime.status === "READY" || runtime.status === "BUSY" ? (runtime.runtime_id ?? null) : null;
  const stale = useMemo(() => {
    const out = new Set<string>();
    for (const cell of cells) {
      if (isStale(cell, runs[cell.id], generation, movedStale, liveRuntimeId)) out.add(cell.id);
    }
    return out;
  }, [cells, generation, liveRuntimeId, movedStale, runs]);

  return {
    loading,
    loadError,
    reload: load,
    title,
    cells,
    saveState,
    flush,
    runtime,
    limits,
    runs,
    stale,
    variables,
    packageEvents,
    canUndo: undoDepth > 0,
    updateSource,
    updateCell,
    insertCell,
    deleteCell,
    moveCell,
    duplicateCell,
    undo,
    run,
    cancel,
    startRuntime,
    stopRuntime,
    restartRuntime,
    clearOutputs,
  };
}

export type NotebookModel = ReturnType<typeof useNotebook>;
