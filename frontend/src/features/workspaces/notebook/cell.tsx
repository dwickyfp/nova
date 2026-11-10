import { memo, useEffect, useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  ArrowDownToLine,
  ArrowUpToLine,
  ChevronDown,
  ChevronUp,
  Copy,
  Eraser,
  GripVertical,
  ListEnd,
  ListStart,
  MoreHorizontal,
  Play,
  Square,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/features/assistant/markdown";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { CellEditor, type CellKeyAction } from "./cell-editor";
import { formatDuration } from "./helpers";
import { CellOutputs } from "./outputs";
import type { CellOutput, CellKind, NotebookCell, SqlEngine } from "./types";
import type { CellRun } from "./use-notebook";

const KIND_LABEL: Record<CellKind, string> = {
  python: "Python",
  sql: "SQL",
  markdown: "Markdown",
};

function gutterLabel(run: CellRun | undefined): string {
  if (!run) return "[ ]";
  if (run.status === "running" || run.status === "queued") return "[*]";
  if (run.executionCount) return `[${run.executionCount}]`;
  return "[ ]";
}

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);
  return <span className="tabular-nums">{((now - since) / 1000).toFixed(1)}s</span>;
}

function statusLine(run: CellRun | undefined, stale: boolean, runtimeReady: boolean) {
  if (!run) return runtimeReady ? null : null;
  switch (run.status) {
    case "queued":
      return <span>Queued</span>;
    case "running":
      return (
        <span className="text-info-strong">
          Running {run.startedAt ? <Elapsed since={run.startedAt} /> : null}
        </span>
      );
    case "succeeded":
      return (
        <span className={stale ? "text-warning-strong" : undefined}>
          {stale ? "Out of date, run again to update" : `Ran in ${formatDuration(run.durationMs)}`}
        </span>
      );
    case "failed":
      return <span className="text-destructive">Failed{stale ? ", code changed since" : ""}</span>;
    case "timeout":
      return <span className="text-destructive">Stopped: took too long</span>;
    case "cancelled":
      return <span>{run.message ?? "Stopped"}</span>;
    case "skipped":
      return <span>Skipped because an earlier cell failed</span>;
    case "lost":
      return <span className="text-destructive">The runtime stopped while this ran</span>;
    default:
      return null;
  }
}

export type CellCommand =
  | "run"
  | "run-above"
  | "run-below"
  | "stop"
  | "move-up"
  | "move-down"
  | "move-top"
  | "move-bottom"
  | "duplicate"
  | "clear"
  | "ask-neva"
  | "delete";

type CellProps = {
  cell: NotebookCell;
  index: number;
  count: number;
  run: CellRun | undefined;
  stale: boolean;
  active: boolean;
  editing: boolean;
  runtimeReady: boolean;
  canEdit: boolean;
  onSelect: (cellId: string, edit: boolean) => void;
  onSource: (cellId: string, source: string) => void;
  onKind: (cellId: string, kind: CellKind) => void;
  onName: (cellId: string, name: string | null) => void;
  onEngine: (cellId: string, engine: SqlEngine) => void;
  onKey: (cellId: string, action: CellKeyAction) => void;
  onCommand: (cellId: string, command: CellCommand) => void;
  tableActions?: (cell: NotebookCell, output: Extract<CellOutput, { type: "table" }>) => React.ReactNode;
};

function NameField({
  name,
  onCommit,
}: {
  name: string | null;
  onCommit: (name: string | null) => void;
}) {
  const [draft, setDraft] = useState(name ?? "");
  useEffect(() => setDraft(name ?? ""), [name]);
  const valid = draft === "" || /^[A-Za-z_][A-Za-z0-9_]*$/.test(draft);
  return (
    <input
      aria-label="Result name"
      title="Name of this result in Python and in {{name}} references"
      value={draft}
      spellCheck={false}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        if (!valid) {
          setDraft(name ?? "");
          return;
        }
        if ((draft || null) !== name) onCommit(draft || null);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") (event.target as HTMLInputElement).blur();
        if (event.key === "Escape") {
          setDraft(name ?? "");
          (event.target as HTMLInputElement).blur();
        }
      }}
      placeholder="name"
      className={cn(
        "h-6 w-24 min-w-0 rounded border @lg/cellhead:w-36  border-transparent bg-transparent px-1.5 font-mono text-xs text-foreground outline-none hover:border-surface-border focus:border-ring",
        !valid && "border-destructive",
      )}
    />
  );
}

export const NotebookCellView = memo(function NotebookCellView({
  cell,
  index,
  count,
  run,
  stale,
  active,
  editing,
  runtimeReady,
  canEdit,
  onSelect,
  onSource,
  onKind,
  onName,
  onEngine,
  onKey,
  onCommand,
  tableActions,
}: CellProps) {
  const running = run?.status === "running" || run?.status === "queued";
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id: cell.id, disabled: running || !canEdit });
  const executable = cell.kind !== "markdown";
  const showEditor = cell.kind !== "markdown" || editing;
  const label = KIND_LABEL[cell.kind];

  return (
    <div
      ref={setNodeRef}
      data-cell-id={cell.id}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={cn("group/cell relative flex gap-2", isDragging && "z-20 opacity-60")}
    >
      <div className="flex w-12 shrink-0 flex-col items-end gap-1 pt-2.5">
        <button
          ref={setActivatorNodeRef}
          type="button"
          {...attributes}
          {...listeners}
          aria-roledescription="Movable cell"
          aria-keyshortcuts="ArrowUp ArrowDown Home End"
          aria-label={`Move ${label} cell ${index + 1} of ${count}`}
          title={
            running
              ? "A running cell cannot be moved"
              : "Drag to move, or press the arrow keys, Home or End"
          }
          onKeyDown={(event) => {
            const move: Record<string, CellCommand> = {
              ArrowUp: "move-up",
              ArrowDown: "move-down",
              Home: "move-top",
              End: "move-bottom",
            };
            const action = move[event.key];
            if (!action || running || !canEdit) return;
            if ((action === "move-up" || action === "move-top") && index === 0) return;
            if ((action === "move-down" || action === "move-bottom") && index === count - 1) return;
            event.preventDefault();
            event.stopPropagation();
            onCommand(cell.id, action);
          }}
          disabled={running || !canEdit}
          className={cn(
            "flex size-6 cursor-grab items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground focus-visible:opacity-100 active:cursor-grabbing disabled:cursor-not-allowed group-hover/cell:opacity-100",
            active && "opacity-100",
          )}
        >
          <GripVertical className="size-4" />
        </button>
        {executable ? (
          <span
            className={cn(
              "font-mono text-xs tabular-nums",
              running ? "text-info-strong" : "text-muted-foreground",
            )}
            aria-label={running ? "Running" : run?.executionCount ? `Execution ${run.executionCount}` : "Not run"}
          >
            {gutterLabel(run)}
          </span>
        ) : null}
      </div>

      <div
        className={cn(
          "@container/cellbody min-w-0 flex-1 rounded-lg border bg-card transition-colors",
          active ? "border-ring/60" : "border-surface-border",
          run?.status === "failed" && !stale && "border-destructive/40",
        )}
        onMouseDown={() => onSelect(cell.id, false)}
      >
        <div className="@container/cellhead flex h-9 min-w-0 items-center gap-2 border-b border-surface-border px-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild disabled={!canEdit}>
              <button
                type="button"
                className="flex h-6 items-center gap-1 rounded px-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label={`Cell type: ${label}`}
              >
                {label}
                <ChevronDown className="size-3" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              {(["python", "sql", "markdown"] as const).map((kind) => (
                <DropdownMenuItem key={kind} onSelect={() => onKind(cell.id, kind)}>
                  {KIND_LABEL[kind]}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          {cell.kind === "sql" ? (
            <>
              <NameField name={cell.name} onCommit={(name) => onName(cell.id, name)} />
              <div
                role="radiogroup"
                aria-label="Run this SQL in"
                className="flex shrink-0 rounded-md bg-muted p-0.5"
              >
                {(["duckdb", "nova"] as const).map((engine) => (
                  <button
                    key={engine}
                    type="button"
                    role="radio"
                    aria-checked={(cell.engine ?? "duckdb") === engine}
                    disabled={!canEdit}
                    title={
                      engine === "duckdb"
                        ? "Run in this notebook's DuckDB: local data, nova.* tables and {{name}} references"
                        : "Send the whole query to Nova: it runs next to the data, read-only, as your role"
                    }
                    onClick={() => onEngine(cell.id, engine)}
                    className={cn(
                      "rounded px-2 py-0.5 text-xs transition-colors",
                      (cell.engine ?? "duckdb") === engine
                        ? "bg-background text-foreground shadow-xs"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {engine === "duckdb" ? "DuckDB" : "Nova"}
                  </button>
                ))}
              </div>
            </>
          ) : cell.kind === "python" && cell.name ? (
            <span className="font-mono text-xs text-muted-foreground">{cell.name}</span>
          ) : null}
          <span className="ml-1 hidden min-w-0 flex-1 truncate text-xs text-muted-foreground @md/cellhead:block">
            {statusLine(run, stale, runtimeReady)}
          </span>
          <div className="ml-auto flex shrink-0 items-center gap-0.5">
            {run?.status === "failed" && !running ? (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
                onClick={() => onCommand(cell.id, "ask-neva")}
              >
                Ask Neva
              </Button>
            ) : null}
            {executable ? (
              running ? (
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-7"
                  aria-label="Stop this cell"
                  title="Stop"
                  onClick={() => onCommand(cell.id, "stop")}
                >
                  <Square className="size-3.5" />
                </Button>
              ) : (
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-7 text-muted-foreground hover:text-foreground"
                  aria-label="Run this cell"
                  title="Run (Ctrl+Enter)"
                  disabled={!canEdit}
                  onClick={() => onCommand(cell.id, "run")}
                >
                  <Play className="size-3.5" />
                </Button>
              )
            ) : null}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-7 text-muted-foreground hover:text-foreground"
                  aria-label="Cell actions"
                >
                  <MoreHorizontal className="size-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                {executable ? (
                  <>
                    <DropdownMenuItem disabled={!canEdit} onSelect={() => onCommand(cell.id, "run-above")}>
                      <ListStart className="size-4" />
                      Run cells above
                    </DropdownMenuItem>
                    <DropdownMenuItem disabled={!canEdit} onSelect={() => onCommand(cell.id, "run-below")}>
                      <ListEnd className="size-4" />
                      Run this and below
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                  </>
                ) : null}
                <DropdownMenuItem disabled={index === 0 || running || !canEdit} onSelect={() => onCommand(cell.id, "move-up")}>
                  <ChevronUp className="size-4" />
                  Move up
                </DropdownMenuItem>
                <DropdownMenuItem disabled={index === count - 1 || running || !canEdit} onSelect={() => onCommand(cell.id, "move-down")}>
                  <ChevronDown className="size-4" />
                  Move down
                </DropdownMenuItem>
                <DropdownMenuItem disabled={index === 0 || running || !canEdit} onSelect={() => onCommand(cell.id, "move-top")}>
                  <ArrowUpToLine className="size-4" />
                  Move to top
                </DropdownMenuItem>
                <DropdownMenuItem disabled={index === count - 1 || running || !canEdit} onSelect={() => onCommand(cell.id, "move-bottom")}>
                  <ArrowDownToLine className="size-4" />
                  Move to bottom
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem disabled={!canEdit} onSelect={() => onCommand(cell.id, "duplicate")}>
                  <Copy className="size-4" />
                  Duplicate
                </DropdownMenuItem>
                {executable ? (
                  <DropdownMenuItem disabled={!run || running} onSelect={() => onCommand(cell.id, "clear")}>
                    <Eraser className="size-4" />
                    Clear output
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuItem
                  disabled={running || !canEdit}
                  className="text-destructive focus:text-destructive"
                  onSelect={() => onCommand(cell.id, "delete")}
                >
                  <Trash2 className="size-4" />
                  Delete
                  <DropdownMenuShortcut>D D</DropdownMenuShortcut>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        {run ? (
          <p className="truncate border-b border-surface-border px-3 py-1 text-xs text-muted-foreground @md/cellbody:hidden">
            {statusLine(run, stale, runtimeReady)}
          </p>
        ) : null}
        {showEditor ? (
          <CellEditor
            cellId={cell.id}
            kind={cell.kind}
            value={cell.source}
            active={active && editing}
            readOnly={!canEdit}
            onActivate={() => onSelect(cell.id, true)}
            onChange={(value) => onSource(cell.id, value)}
            onKey={(action) => onKey(cell.id, action)}
          />
        ) : (
          <div
            role="button"
            tabIndex={-1}
            aria-label="Edit Markdown cell"
            className="cursor-text px-4 py-3 text-sm"
            onDoubleClick={() => onSelect(cell.id, true)}
          >
            {cell.source.trim() ? (
              <Markdown offline>{cell.source}</Markdown>
            ) : (
              <p className="text-muted-foreground">Double-click to write notes</p>
            )}
          </div>
        )}

        {executable && run && run.outputs.length > 0 ? (
          <div
            className={cn(
              "border-t border-surface-border px-3 py-3",
              stale && "opacity-70",
            )}
          >
            <CellOutputs
              outputs={run.outputs}
              tableActions={tableActions ? (output) => tableActions(cell, output) : undefined}
            />
          </div>
        ) : executable && run?.outputsPending ? (
          <p className="border-t border-surface-border px-3 py-2 text-xs text-muted-foreground">
            The last output is large; run the cell to see it again
          </p>
        ) : null}
      </div>
    </div>
  );
});
