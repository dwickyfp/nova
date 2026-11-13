import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  PointerSensor,
  closestCorners,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import {
  Braces,
  ChevronDown,
  Download,
  Eraser,
  FileText,
  Keyboard,
  ListOrdered,
  PanelRight,
  Play,
  Plus,
  RotateCcw,
  Square,
  TableProperties,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { apiBase, authHeaders } from "@/lib/api-client";
import { AddToDatavizButton } from "@/features/studio/dataviz/add-to-dataviz";
import { cn } from "@/lib/utils";
import { RewriteReview } from "../rewrite-review";
import { notebookApi } from "./api";
import { cellForFile, isLive, NOVA_TABLE_MIME } from "./helpers";
import { NotebookCellView, type CellCommand } from "./cell";
import type { CellKeyAction } from "./cell-editor";
import { RuntimeControl } from "./runtime-control";
import { NotebookNevaSurface, type CellProposal } from "./neva";
import { NotebookSidePanel, type SidePanelTab } from "./side-panel";
import type { CellKind, CellOutput, NotebookCell, RunMode, SqlEngine } from "./types";
import { useNotebook, type NotebookModel } from "./use-notebook";

const SAVE_LABEL: Record<NotebookModel["saveState"], string> = {
  saved: "Saved",
  saving: "Saving",
  unsaved: "Unsaved changes",
  conflict: "Changed in another tab",
  error: "Not saved, retrying",
};

function quoteIdentifier(name: string) {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? name : `\`${name.replace(/`/g, "``")}\``;
}

export function NotebookEditor({ notebookId }: { notebookId: string }) {
  const model = useNotebook(notebookId);
  const {
    cells,
    runs,
    runtime,
    stale,
    insertCell,
    moveCell,
    deleteCell,
  } = model;
  const [activeId, setActiveId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [panel, setPanel] = useState<SidePanelTab | null>(null);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [dropActive, setDropActive] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [width, setWidth] = useState(0);
  const [proposal, setProposal] = useState<CellProposal | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const lastKey = useRef<{ key: string; at: number } | null>(null);
  const askNeva = useRef<((prompt: string) => void) | null>(null);
  const registerAsk = useCallback((ask: (prompt: string) => void) => {
    askNeva.current = ask;
  }, []);

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const ready = runtime.status === "READY" || runtime.status === "BUSY";
  const anyRunning = Object.values(runs).some(
    (run) => run.status === "running" || run.status === "queued",
  );
  const canRun = model.saveState !== "conflict";
  const canEdit = canRun && !model.viewOnly;

  const select = useCallback((cellId: string, edit: boolean) => {
    setActiveId(cellId);
    setEditing(edit);
  }, []);

  const focusCanvas = useCallback(() => {
    containerRef.current?.focus({ preventScroll: true });
  }, []);

  const ensureRuntime = useCallback(async () => {
    if (isLive(runtime.status)) return;
    toast.message("Starting the runtime first", {
      description: "Python and DuckDB start in an isolated runtime for this notebook",
    });
    await model.startRuntime();
  }, [model, runtime.status]);

  const runCells = useCallback(
    async (mode: RunMode, cellId?: string, onError: "stop" | "continue" = "stop") => {
      try {
        await ensureRuntime();
        const response = await model.run(mode, cellId, onError);
        if (!response.executions.length) {
          toast.message("Nothing to run", { description: "There are no Python or SQL cells with code here" });
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "The cells could not run");
      }
    },
    [ensureRuntime, model],
  );

  const announce = useCallback((text: string) => setAnnouncement(text), []);

  const command = useCallback(
    (cellId: string, action: CellCommand) => {
      const index = cells.findIndex((c) => c.id === cellId);
      switch (action) {
        case "run":
          void runCells("cell", cellId);
          break;
        case "run-above":
          void runCells("above", cellId);
          break;
        case "run-below":
          void runCells("below", cellId);
          break;
        case "stop": {
          const run = runs[cellId];
          if (run) void model.cancel(run.executionId);
          break;
        }
        case "move-up":
          moveCell(cellId, index - 1);
          announce(`Moved cell ${index + 1} to position ${index} of ${cells.length}`);
          break;
        case "move-down":
          moveCell(cellId, index + 1);
          announce(`Moved cell ${index + 1} to position ${index + 2} of ${cells.length}`);
          break;
        case "move-top":
          moveCell(cellId, 0);
          announce(`Moved cell ${index + 1} to the top`);
          break;
        case "move-bottom":
          moveCell(cellId, cells.length - 1);
          announce(`Moved cell ${index + 1} to the bottom`);
          break;
        case "duplicate": {
          const id = model.duplicateCell(cellId);
          if (id) select(id, false);
          break;
        }
        case "clear":
          model.clearOutputs(cellId);
          break;
        case "ask-neva":
          askNeva.current?.(
            `Explain why cell ${index + 1} failed and propose a fix for it.`,
          );
          break;
        case "delete": {
          const next = cells[index + 1] ?? cells[index - 1];
          deleteCell(cellId);
          setActiveId(next?.id ?? null);
          toast.message("Cell deleted", {
            action: { label: "Undo", onClick: () => model.undo() },
          });
          break;
        }
      }
    },
    [announce, cells, deleteCell, model, moveCell, runCells, runs, select],
  );

  const addCell = useCallback(
    (kind: CellKind, index: number, source = "", title: string | null = null) => {
      const id = insertCell(kind, index, source, null, "duckdb", title);
      setActiveId(id);
      setEditing(kind !== "markdown" || !source);
      return id;
    },
    [insertCell],
  );

  const onCellKey = useCallback(
    (cellId: string, action: CellKeyAction) => {
      const index = cells.findIndex((c) => c.id === cellId);
      if (action === "escape") {
        setEditing(false);
        focusCanvas();
        return;
      }
      const cell = cells[index];
      if (cell && cell.kind !== "markdown") void runCells("cell", cellId);
      if (cell?.kind === "markdown") setEditing(false);
      if (action === "run-next") {
        const next = cells[index + 1];
        if (next) select(next.id, next.kind !== "markdown");
        else addCell("python", index + 1);
      } else if (action === "run-insert") {
        addCell("python", index + 1);
      }
    },
    [addCell, cells, focusCanvas, runCells, select],
  );

  // Cells are memoized: hand them handlers that never change identity, so
  // typing in one cell re-renders that cell only, not the whole notebook.
  const latest = useRef({ onCellKey, command, updateCell: model.updateCell });
  latest.current = { onCellKey, command, updateCell: model.updateCell };
  const cellHandlers = useMemo(
    () => ({
      onKind: (id: string, kind: CellKind) => latest.current.updateCell(id, { kind }),
      onName: (id: string, name: string | null) => latest.current.updateCell(id, { name }),
      onTitle: (id: string, title: string | null) => latest.current.updateCell(id, { title }),
      onEngine: (id: string, engine: SqlEngine) =>
        latest.current.updateCell(id, { engine }),
      onKey: (id: string, action: CellKeyAction) => latest.current.onCellKey(id, action),
      onCommand: (id: string, action: CellCommand) => latest.current.command(id, action),
    }),
    [],
  );

  // Command mode: keys on the canvas while no editor has focus.
  const onCanvasKey = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const target = event.target as HTMLElement;
      if (target.closest('.monaco-editor, [role="combobox"], [role="listbox"]') || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
        return;
      }
      const meta = event.metaKey || event.ctrlKey;
      const key = event.key.toLowerCase();
      if (meta && key === "z" && !event.shiftKey && !target.closest("[role=menu]")) {
        event.preventDefault();
        const label = model.undo();
        if (label) announce(`Undid: ${label}`);
        return;
      }
      // Menus and drag handles own their other keys (a handle moves its cell).
      if (target.closest("[role=menu]") || target.closest("[aria-roledescription]")) return;
      const index = activeId ? cells.findIndex((c) => c.id === activeId) : -1;
      const cell = index >= 0 ? cells[index] : null;
      const now = Date.now();
      if (event.key === "Enter" && event.shiftKey && cell) {
        event.preventDefault();
        onCellKey(cell.id, "run-next");
        return;
      }
      if (event.key === "Enter" && meta && cell) {
        event.preventDefault();
        onCellKey(cell.id, "run");
        return;
      }
      if (meta && event.shiftKey && cell && canEdit && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
        event.preventDefault();
        const up = event.key === "ArrowUp";
        if (up ? index > 0 : index < cells.length - 1) command(cell.id, up ? "move-up" : "move-down");
        return;
      }
      if (meta || event.altKey) return;
      if (event.key === "Enter" && cell) {
        event.preventDefault();
        setEditing(true);
        return;
      }
      if (event.key === "ArrowDown" || key === "j") {
        event.preventDefault();
        const next = cells[Math.min(cells.length - 1, index + 1)];
        if (next) select(next.id, false);
        return;
      }
      if (event.key === "ArrowUp" || key === "k") {
        event.preventDefault();
        const previous = cells[Math.max(0, index - 1)];
        if (previous) select(previous.id, false);
        return;
      }
      if (!canEdit) return;
      if (key === "a") {
        event.preventDefault();
        addCell("python", Math.max(0, index));
      } else if (key === "b") {
        event.preventDefault();
        addCell("python", index + 1);
      } else if (cell && (key === "m" || key === "y" || key === "s")) {
        event.preventDefault();
        model.updateCell(cell.id, { kind: key === "m" ? "markdown" : key === "y" ? "python" : "sql" });
      } else if (cell && key === "d") {
        event.preventDefault();
        if (lastKey.current?.key === "d" && now - lastKey.current.at < 600) {
          command(cell.id, "delete");
          lastKey.current = null;
          return;
        }
      } else if (event.key === "?") {
        setShortcutsOpen(true);
      }
      lastKey.current = { key, at: now };
    },
    [activeId, addCell, announce, canEdit, cells, command, model, onCellKey, select],
  );

  // ── Drag and drop: cells, data files and catalog tables ────────────────
  // Pointer drags use dnd-kit; keys on a handle move the cell directly
  // (arrow keys, Home, End), which stays exact for cells of any height.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  const position = useCallback(
    (id: string | number) => cells.findIndex((c) => c.id === id) + 1,
    [cells],
  );
  const announcements: Announcements = useMemo(
    () => ({
      onDragStart: ({ active }) => `Picked up cell ${position(active.id)}`,
      onDragOver: ({ active, over }) =>
        over ? `Cell ${position(active.id)} is over position ${position(over.id)}` : "",
      onDragEnd: ({ active, over }) =>
        over ? `Moved cell ${position(active.id)} to position ${position(over.id)}` : "Move cancelled",
      onDragCancel: ({ active }) => `Move cancelled, cell ${position(active.id)} returned`,
    }),
    [position],
  );

  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;
      const to = cells.findIndex((c) => c.id === over.id);
      if (to >= 0) {
        moveCell(String(active.id), to);
      }
    },
    [cells, moveCell],
  );

  const insertIndexFromEvent = useCallback(
    (event: React.DragEvent) => {
      const nodes = Array.from(
        containerRef.current?.querySelectorAll<HTMLElement>("[data-cell-id]") ?? [],
      );
      const index = nodes.findIndex((node) => {
        const box = node.getBoundingClientRect();
        return event.clientY < box.top + box.height / 2;
      });
      return index === -1 ? cells.length : index;
    },
    [cells.length],
  );

  const onDrop = useCallback(
    async (event: React.DragEvent) => {
      setDropActive(false);
      const table = event.dataTransfer.getData(NOVA_TABLE_MIME);
      const index = insertIndexFromEvent(event);
      if (table) {
        event.preventDefault();
        try {
          const ref = JSON.parse(table) as { database: string; schema: string; name: string };
          const qualified = [ref.database, ref.schema, ref.name].filter(Boolean).map(quoteIdentifier).join(".");
          addCell("sql", index, `SELECT *\nFROM ${qualified}\nLIMIT 100`, "nova");
          toast.message("Preview query added", { description: "Run it to see the first rows; nothing is copied until you do" });
        } catch {
          toast.error("That item cannot be added to a notebook");
        }
        return;
      }
      const files = Array.from(event.dataTransfer.files ?? []);
      if (!files.length) return;
      event.preventDefault();
      for (const file of files) {
        const lower = file.name.toLowerCase();
        if (lower.endsWith(".sql")) {
          addCell("sql", index, await file.text());
          continue;
        }
        const folder = lower.endsWith(".py") ? "src" : "data";
        const pending = toast.loading(`Uploading ${file.name}`);
        try {
          const uploaded = await notebookApi.upload(notebookId, file, folder);
          const starter = cellForFile(uploaded.relative_path);
          if (starter) {
            addCell(starter.kind, index, starter.source);
            toast.success(`${file.name} added`, { id: pending, description: `Saved to ${uploaded.relative_path}` });
          } else {
            toast.success(`${file.name} saved to ${uploaded.relative_path}`, {
              id: pending,
              description: lower.endsWith(".py")
                ? `Import it in Python with: import ${file.name.replace(/\.py$/i, "")}`
                : undefined,
            });
          }
        } catch (error) {
          toast.error(error instanceof Error ? error.message : `${file.name} could not be uploaded`, { id: pending });
        }
      }
    },
    [addCell, insertIndexFromEvent, model, notebookId],
  );

  async function exportNotebook() {
    try {
      await model.flush();
      const response = await fetch(`${apiBase()}${notebookApi.exportUrl(notebookId)}`, {
        headers: authHeaders(),
      });
      if (!response.ok) throw new Error("export");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = model.title || "notebook.ipynb";
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("The notebook could not be exported");
    }
  }

  const focusCell = useCallback(
    (cellId: string) => {
      select(cellId, false);
      containerRef.current
        ?.querySelector(`[data-cell-id="${cellId}"]`)
        ?.scrollIntoView({ block: "center" });
    },
    [select],
  );

  const applyProposal = useCallback(
    (run: boolean) => {
      if (!proposal) return;
      let cellId = proposal.targetCellId;
      const target = cells.find((c) => c.id === proposal.targetCellId);
      if (proposal.mode === "replace" && target) {
        if (target.kind !== proposal.kind || (proposal.title && proposal.title !== target.title)) {
          model.updateCell(target.id, {
            kind: proposal.kind,
            ...(proposal.title ? { title: proposal.title } : {}),
          });
        }
        model.updateSource(target.id, proposal.source);
      } else {
        const index = target ? cells.findIndex((c) => c.id === target.id) + 1 : cells.length;
        cellId = addCell(proposal.kind, index, proposal.source, proposal.title);
        setEditing(false);
      }
      setProposal(null);
      if (run && proposal.kind !== "markdown") void runCells("cell", cellId);
    },
    [addCell, cells, model, proposal, runCells],
  );

  // A table whose query reads only Nova catalogs can become a Dataviz chart:
  // the worker checked that Nova runs it (``nova_sql``), and Dataviz confirms
  // again before showing the button.
  const tableActions = useCallback(
    (cell: NotebookCell, output: Extract<CellOutput, { type: "table" }>) => {
      const run = runs[cell.id];
      if (
        cell.kind !== "sql" ||
        !output.table.nova_sql ||
        !run ||
        run.status !== "succeeded" ||
        stale.has(cell.id)
      ) {
        return null;
      }
      return (
        <AddToDatavizButton
          locator={{
            thread_id: `notebook:${notebookId}`,
            message_id: run.executionId,
            content_id: "table",
          }}
        />
      );
    },
    [notebookId, runs, stale],
  );

  const outline = useMemo(
    () =>
      cells.map((cell, index) => {
        const heading =
          cell.kind === "markdown" ? cell.source.match(/^\s*#{1,6}\s+(.+)$/m)?.[1]?.trim() : undefined;
        const fallback = `${cell.kind === "sql" ? "SQL" : cell.kind === "python" ? "Python" : "Note"} cell ${index + 1}`;
        return {
          id: cell.id,
          label: cell.title || heading || fallback,
          titled: Boolean(cell.title || heading),
          active: cell.id === activeId,
        };
      }),
    [activeId, cells],
  );

  const narrow = width > 0 && width < 900;

  if (model.loading) {
    return <p className="px-6 py-6 text-sm text-muted-foreground">Opening notebook</p>;
  }
  if (model.loadError) {
    return (
      <div className="px-6 py-6">
        <EmptyState
          variant="error"
          title="The notebook could not be opened"
          description={model.loadError}
          action={<Button size="sm" variant="outline" onClick={() => void model.reload()}>Try again</Button>}
        />
      </div>
    );
  }

  return (
    <div className="@container/editor relative flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-surface-border px-4 py-2">
        <div className="flex shrink-0 items-center gap-2">
          {anyRunning ? (
            <Button size="sm" variant="outline" onClick={() => void model.cancel()}>
              <Square className="size-4" />
              Stop
            </Button>
          ) : (
            <div className="flex gap-px">
              <Button
                size="sm"
                className="rounded-r-none"
                disabled={!canRun || !cells.length}
                onClick={() => void runCells("all")}
              >
                <Play className="size-4" />
                Run all
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="sm" className="rounded-l-none px-2" aria-label="More ways to run" disabled={!canRun}>
                    <ChevronDown className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-64">
                  <DropdownMenuItem onSelect={() => void runCells("all", undefined, "continue")}>
                    <ListOrdered className="size-4" />
                    Run all, continue after errors
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    disabled={!activeId}
                    onSelect={() => activeId && void runCells("above", activeId)}
                  >
                    Run cells above the selected one
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    disabled={!activeId}
                    onSelect={() => activeId && void runCells("below", activeId)}
                  >
                    Run selected and below
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={() => model.clearOutputs()}>
                    <Eraser className="size-4" />
                    Clear all outputs
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant="outline" disabled={!canEdit}>
                <Plus className="size-4" />
                Add cell
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuItem onSelect={() => addCell("python", cells.length)}>
                <Braces className="size-4" />
                Python
                <DropdownMenuShortcut>Y</DropdownMenuShortcut>
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => addCell("sql", cells.length)}>
                <TableProperties className="size-4" />
                SQL
                <DropdownMenuShortcut>S</DropdownMenuShortcut>
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => addCell("markdown", cells.length)}>
                <FileText className="size-4" />
                Markdown
                <DropdownMenuShortcut>M</DropdownMenuShortcut>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button
            size="icon"
            variant="ghost"
            className="size-8 text-muted-foreground"
            aria-label="Undo the last cell change"
            title="Undo the last cell change"
            disabled={!model.canUndo}
            onClick={() => {
              const label = model.undo();
              if (label) announce(`Undid: ${label}`);
            }}
          >
            <Undo2 className="size-4" />
          </Button>
        </div>
        <span
          className={cn(
            "text-xs",
            model.saveState === "conflict" || model.saveState === "error"
              ? "text-destructive"
              : "text-muted-foreground",
          )}
          role="status"
        >
          {model.viewOnly ? "View only, you can run it" : SAVE_LABEL[model.saveState]}
        </span>
        <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-1">
          <RuntimeControl
            notebookId={notebookId}
            runtime={runtime}
            limits={model.limits}
            anyRunning={anyRunning}
            onStart={model.startRuntime}
            onStop={model.stopRuntime}
            onRestart={model.restartRuntime}
          />
          <Button
            size="icon"
            variant="ghost"
            className="size-8 text-muted-foreground"
            aria-label="Download as .ipynb"
            title="Download as .ipynb"
            onClick={() => void exportNotebook()}
          >
            <Download className="size-4" />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="size-8 text-muted-foreground"
            aria-label="Keyboard shortcuts"
            title="Keyboard shortcuts"
            onClick={() => setShortcutsOpen(true)}
          >
            <Keyboard className="size-4" />
          </Button>
          <Button
            size="icon"
            variant={panel ? "secondary" : "ghost"}
            className="size-8 text-muted-foreground"
            aria-label="Outline, variables and packages"
            title="Outline, variables and packages"
            aria-pressed={!!panel}
            onClick={() => setPanel((current) => (current ? null : "outline"))}
          >
            <PanelRight className="size-4" />
          </Button>
        </div>
      </div>

      {model.saveState === "conflict" ? (
        <div role="alert" className="flex items-center gap-3 border-b border-warning/30 bg-warning/10 px-4 py-2 text-sm text-warning-strong">
          This notebook changed in another tab. Your last edits were not saved.
          <Button size="sm" variant="outline" className="ml-auto" onClick={() => void model.reload()}>
            <RotateCcw className="size-4" />
            Load the latest
          </Button>
        </div>
      ) : null}
      {runtime.status === "LOST" ? (
        <div role="alert" className="border-b border-destructive/25 bg-destructive/5 px-4 py-2 text-sm text-destructive">
          The runtime stopped. Files are saved, but variables have to be computed again.
        </div>
      ) : null}

      <div className="relative flex min-h-0 flex-1">
        <div
          ref={containerRef}
          tabIndex={0}
          role="list"
          aria-label="Notebook cells"
          onKeyDown={onCanvasKey}
          onDragOver={(event) => {
            const types = event.dataTransfer.types;
            if (types.includes("Files") || types.includes(NOVA_TABLE_MIME)) {
              event.preventDefault();
              event.dataTransfer.dropEffect = "copy";
              setDropActive(true);
            }
          }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node)) setDropActive(false);
          }}
          onDrop={(event) => void onDrop(event)}
          className={cn(
            "min-h-0 flex-1 overflow-y-auto outline-none",
            dropActive && "bg-surface-1",
          )}
        >
          <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-0 px-3 py-4 pr-4">
            {cells.length === 0 ? (
              <EmptyState
                title="Start an analysis"
                description="Add a SQL, Python or Markdown cell, or drop a CSV or Parquet file here"
                action={
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => addCell("sql", 0)}>SQL</Button>
                    <Button size="sm" variant="outline" onClick={() => addCell("python", 0)}>Python</Button>
                    <Button size="sm" variant="outline" onClick={() => addCell("markdown", 0)}>Markdown</Button>
                  </div>
                }
              />
            ) : (
              <DndContext
                sensors={sensors}
                collisionDetection={closestCorners}
                onDragEnd={onDragEnd}
                accessibility={{ announcements }}
              >
                <SortableContext items={cells.map((c) => c.id)} strategy={verticalListSortingStrategy}>
                  {cells.map((cell, index) => (
                    <div key={cell.id} role="listitem">
                      {proposal?.mode === "replace" && proposal.targetCellId === cell.id ? (
                        <ProposalReview
                          proposal={proposal}
                          onCancel={() => setProposal(null)}
                          onApply={() => applyProposal(false)}
                          onApplyAndRun={() => applyProposal(true)}
                        />
                      ) : null}
                      <NotebookCellView
                        cell={cell}
                        index={index}
                        count={cells.length}
                        run={runs[cell.id]}
                        stale={stale.has(cell.id)}
                        active={activeId === cell.id}
                        editing={activeId === cell.id && editing}
                        canEdit={canEdit}
                        canRun={canRun}
                        onSelect={select}
                        onSource={model.updateSource}
                        onKind={cellHandlers.onKind}
                        onName={cellHandlers.onName}
                        onTitle={cellHandlers.onTitle}
                        onEngine={cellHandlers.onEngine}
                        onKey={cellHandlers.onKey}
                        onCommand={cellHandlers.onCommand}
                        tableActions={tableActions}
                      />
                      {proposal?.mode === "insert_below" && proposal.targetCellId === cell.id ? (
                        <ProposalReview
                          proposal={proposal}
                          onCancel={() => setProposal(null)}
                          onApply={() => applyProposal(false)}
                          onApplyAndRun={() => applyProposal(true)}
                        />
                      ) : null}
                      <InsertBar disabled={!canEdit} onInsert={(kind) => addCell(kind, index + 1)} />
                    </div>
                  ))}
                </SortableContext>
              </DndContext>
            )}
            {dropActive ? (
              <p className="pointer-events-none sticky bottom-4 mx-auto rounded-md border border-surface-border bg-popover px-3 py-1.5 text-sm text-foreground shadow-sm">
                Drop to add to this notebook
              </p>
            ) : null}
          </div>
        </div>
        {panel ? (
          <NotebookSidePanel
            tab={panel}
            onTab={setPanel}
            onClose={() => setPanel(null)}
            notebookId={notebookId}
            variables={model.variables}
            ready={ready}
            packageEvents={model.packageEvents}
            overlay={narrow}
            outline={outline}
            onJump={focusCell}
          />
        ) : null}
      </div>

      <div aria-live="polite" className="sr-only">
        {announcement}
      </div>
      <NotebookNevaSurface
        notebookId={notebookId}
        title={model.title}
        model={model}
        activeId={activeId}
        onPropose={(next) => {
          setProposal(next);
          focusCell(next.targetCellId);
        }}
        onFocusCell={focusCell}
        registerAsk={registerAsk}
      />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </div>
  );
}

function ProposalReview({
  proposal,
  onCancel,
  onApply,
  onApplyAndRun,
}: {
  proposal: CellProposal;
  onCancel: () => void;
  onApply: () => void;
  onApplyAndRun: () => void;
}) {
  const label =
    proposal.mode === "replace"
      ? "Neva suggests changing this cell"
      : `Neva suggests a new ${proposal.kind === "sql" ? "SQL" : proposal.kind === "python" ? "Python" : "Markdown"} cell below`;
  return (
    <div className="my-2 ml-14 flex flex-col gap-1">
      <p className="text-xs text-muted-foreground">
        {label}
        {proposal.title ? `: ${proposal.title}` : ""}
      </p>
      <RewriteReview
        rewrite={proposal.rewrite}
        onCancel={onCancel}
        onApply={onApply}
        onApplyAndRun={onApplyAndRun}
      />
    </div>
  );
}

function InsertBar({
  onInsert,
  disabled,
}: {
  onInsert: (kind: CellKind) => void;
  disabled: boolean;
}) {
  return (
    <div className="group/insert flex h-6 items-center gap-2 pl-7 opacity-0 transition-opacity focus-within:opacity-100 hover:opacity-100">
      <div className="h-px flex-1 bg-surface-border" />
      {(["python", "sql", "markdown"] as const).map((kind) => (
        <button
          key={kind}
          type="button"
          disabled={disabled}
          onClick={() => onInsert(kind)}
          className="rounded px-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          + {kind === "python" ? "Python" : kind === "sql" ? "SQL" : "Markdown"}
        </button>
      ))}
      <div className="h-px flex-1 bg-surface-border" />
    </div>
  );
}

const SHORTCUTS: Array<[string, string]> = [
  ["Shift Enter", "Run the cell and select the next one"],
  ["Ctrl Enter", "Run the cell"],
  ["Alt Enter", "Run the cell and add one below"],
  ["Esc", "Stop editing; keys now act on cells"],
  ["Enter", "Edit the selected cell"],
  ["↑ ↓", "Select the cell above or below"],
  ["A / B", "Add a Python cell above / below"],
  ["Y / S / M", "Make the cell Python / SQL / Markdown"],
  ["D D", "Delete the cell"],
  ["Ctrl Z", "Undo the last cell change"],
];

function ShortcutsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>On a Mac, Ctrl is ⌘</DialogDescription>
        </DialogHeader>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          {SHORTCUTS.map(([keys, label]) => (
            <div key={keys} className="contents">
              <dt className="flex gap-1">
                {keys.split(" ").map((key) => (
                  <kbd key={key} className="rounded border border-surface-border bg-muted px-1.5 font-mono text-xs">
                    {key}
                  </kbd>
                ))}
              </dt>
              <dd className="text-muted-foreground">{label}</dd>
            </div>
          ))}
        </dl>
      </DialogContent>
    </Dialog>
  );
}
