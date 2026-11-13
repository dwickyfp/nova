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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
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

function statusLine(run: CellRun | undefined, stale: boolean) {
  if (!run) return null;
  // Saved output no longer matches the code or the live runtime.
  if (stale && (run.status === "succeeded" || run.status === "failed")) {
    return <span className="text-warning-strong">Out of date, run again to update</span>;
  }
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
      return <span aria-label="Last run duration">{formatDuration(run.durationMs)}</span>;
    case "failed":
      return <span className="text-destructive">Failed</span>;
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
  /** The output no longer shows what this cell would produce now. */
  stale?: boolean;
  active: boolean;
  editing: boolean;
  canEdit: boolean;
  canRun: boolean;
  onSelect: (cellId: string, edit: boolean) => void;
  onSource: (cellId: string, source: string) => void;
  onKind: (cellId: string, kind: CellKind) => void;
  onName: (cellId: string, name: string | null) => void;
  onTitle?: (cellId: string, title: string | null) => void;
  onEngine: (cellId: string, engine: SqlEngine) => void;
  onKey: (cellId: string, action: CellKeyAction) => void;
  onCommand: (cellId: string, command: CellCommand) => void;
  tableActions?: (cell: NotebookCell, output: Extract<CellOutput, { type: "table" }>) => React.ReactNode;
};

/** Text that commits on blur or Enter and reverts on Escape. */
function InlineField({
  value,
  label,
  placeholder,
  valid = () => true,
  onCommit,
  className,
  disabled,
}: {
  value: string | null;
  label: string;
  placeholder: string;
  valid?: (draft: string) => boolean;
  onCommit: (value: string | null) => void;
  className?: string;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => setDraft(value ?? ""), [value]);
  const ok = valid(draft);
  return (
    <input
      aria-label={label}
      value={draft}
      spellCheck={false}
      disabled={disabled}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        if (!ok) {
          setDraft(value ?? "");
          return;
        }
        const next = draft.trim() || null;
        if (next !== (value ?? null)) onCommit(next);
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === "Enter") (event.target as HTMLInputElement).blur();
        if (event.key === "Escape") {
          setDraft(value ?? "");
          (event.target as HTMLInputElement).blur();
        }
      }}
      placeholder={placeholder}
      className={cn(
        "min-w-0 rounded bg-transparent outline-none placeholder:text-muted-foreground/70 focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default",
        !ok && "text-destructive",
        className,
      )}
    />
  );
}

const RESULT_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

function ToolbarButton({
  label,
  shortcut,
  disabled,
  onClick,
  children,
}: {
  label: string;
  shortcut?: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      size="icon"
      variant="ghost"
      className="size-7 text-muted-foreground hover:text-foreground"
      aria-label={label}
      aria-keyshortcuts={shortcut === "Ctrl+Enter" ? "Control+Enter Meta+Enter" : undefined}
      title={shortcut ? `${label} (${shortcut})` : label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}

export const NotebookCellView = memo(function NotebookCellView({
  cell,
  index,
  count,
  run,
  stale = false,
  active,
  editing,
  canEdit,
  canRun,
  onSelect,
  onSource,
  onKind,
  onName,
  onTitle,
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
  const status = executable ? statusLine(run, stale) : null;
  const first = index === 0;
  const last = index === count - 1;

  return (
    <div
      ref={setNodeRef}
      data-cell-id={cell.id}
      onKeyDownCapture={(event) => {
        if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey) ||
            event.shiftKey || event.altKey || event.nativeEvent.isComposing) return;
        const target = event.target as HTMLElement;
        if (target.closest('[role="menu"], [role="listbox"], [role="combobox"]')) return;
        event.preventDefault();
        event.stopPropagation();
        if ((canRun || (!executable && canEdit)) && !running) onKey(cell.id, "run");
      }}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={cn("group/cell relative flex gap-1", isDragging && "z-20 opacity-60")}
    >
      <div className="flex w-6 shrink-0 flex-col items-center pt-1.5">
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
            if ((action === "move-up" || action === "move-top") && first) return;
            if ((action === "move-down" || action === "move-bottom") && last) return;
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
      </div>

      <div
        className="@container/cellbody relative min-w-0 flex-1 rounded-md border border-surface-border border-l-2 border-l-ring bg-card px-2 pb-2"
        onMouseDown={(event) => {
          // A press inside the editor keeps (or starts) editing; switching the
          // editor off and on again around the click made cells flicker and
          // closed a Markdown editor before the caret could land.
          const inEditor = (event.target as HTMLElement).closest("[data-cell-editor], [data-cell-preview]");
          if (inEditor) {
            if (!(active && editing)) onSelect(cell.id, true);
          } else if (!active || editing) {
            onSelect(cell.id, false);
          }
        }}
      >
        <div className="relative flex min-h-9 min-w-0 flex-wrap items-center gap-x-2">
          <Select
            value={cell.kind}
            disabled={!canEdit || running}
            onValueChange={(kind) => onKind(cell.id, kind as CellKind)}
          >
            <SelectTrigger size="sm" aria-label="Cell type" className="h-7 shrink-0 border-0 bg-transparent px-1 text-xs shadow-none dark:bg-transparent dark:hover:bg-muted">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(["python", "sql", "markdown"] as const).map((kind) => (
                <SelectItem key={kind} value={kind}>{KIND_LABEL[kind]}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {onTitle && executable ? (
            <InlineField
              value={cell.title ?? null}
              label="Cell title"
              placeholder="Add a title"
              disabled={!canEdit}
              onCommit={(title) => onTitle(cell.id, title)}
              className="order-3 h-7 w-full max-w-md basis-full px-1 text-xs text-muted-foreground focus:text-foreground @sm/cellbody:order-none @sm/cellbody:w-20 @sm/cellbody:flex-1 @sm/cellbody:basis-auto"
            />
          ) : (
            <span className="flex-1" />
          )}
          <div className="ml-auto flex shrink-0 items-center gap-2">
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
            <span className="hidden max-w-64 truncate text-xs text-muted-foreground @lg/cellbody:block">
              {status}
            </span>
            {executable && run?.status === "failed" && !running ? (
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
                <ToolbarButton label="Stop this cell" onClick={() => onCommand(cell.id, "stop")}>
                  <Square className="size-3.5" />
                </ToolbarButton>
              ) : (
                <ToolbarButton
                  label="Run this cell"
                  shortcut="Ctrl+Enter"
                  disabled={!canRun}
                  onClick={() => onCommand(cell.id, "run")}
                >
                  <Play className="size-3.5" />
                </ToolbarButton>
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
                <DropdownMenuItem disabled={first || running || !canEdit} onSelect={() => onCommand(cell.id, "move-up")}>
                  <ChevronUp className="size-4" />
                  Move up
                  <DropdownMenuShortcut>Ctrl+Shift+Up</DropdownMenuShortcut>
                </DropdownMenuItem>
                <DropdownMenuItem disabled={last || running || !canEdit} onSelect={() => onCommand(cell.id, "move-down")}>
                  <ChevronDown className="size-4" />
                  Move down
                  <DropdownMenuShortcut>Ctrl+Shift+Down</DropdownMenuShortcut>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                {executable ? (
                  <>
                    <DropdownMenuItem disabled={!canRun} onSelect={() => onCommand(cell.id, "run-above")}>
                      <ListStart className="size-4" />
                      Run cells above
                    </DropdownMenuItem>
                    <DropdownMenuItem disabled={!canRun} onSelect={() => onCommand(cell.id, "run-below")}>
                      <ListEnd className="size-4" />
                      Run this and below
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                  </>
                ) : null}
                <DropdownMenuItem disabled={first || running || !canEdit} onSelect={() => onCommand(cell.id, "move-top")}>
                  <ArrowUpToLine className="size-4" />
                  Move to top
                </DropdownMenuItem>
                <DropdownMenuItem disabled={last || running || !canEdit} onSelect={() => onCommand(cell.id, "move-bottom")}>
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

        {status ? (
          <p className="truncate pb-1 text-xs text-muted-foreground @lg/cellbody:hidden">{status}</p>
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
            className="cursor-text px-2 py-2 text-sm"
            onDoubleClick={() => onSelect(cell.id, true)}
          >
            {cell.source.trim() ? (
              <Markdown offline className="[&>:first-child]:mt-0 [&>:last-child]:mb-0">{cell.source}</Markdown>
            ) : (
              <p className="text-muted-foreground">Double-click to write notes</p>
            )}
          </div>
        )}

        {cell.kind === "sql" ? (
          <div className="flex min-h-7 min-w-0 flex-wrap items-center gap-x-2 px-2 font-mono text-xs text-muted-foreground">
            <span aria-hidden>→</span>
            <InlineField
              value={cell.name}
              label="Result name"
              placeholder="name"
              disabled={!canEdit}
              valid={(draft) => draft === "" || RESULT_NAME.test(draft)}
              onCommit={(name) => onName(cell.id, name)}
              className="h-6 w-40 px-1 font-mono text-xs text-muted-foreground focus:text-foreground"
            />
            {cell.engine === "nova" ? (
              <span className="flex min-w-0 items-center gap-2 font-sans">
                <span className="truncate">Runs in Nova</span>
                <button
                  type="button"
                  disabled={!canEdit}
                  className="shrink-0 rounded text-foreground underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => onEngine(cell.id, "duckdb")}
                >
                  Run in DuckDB
                </button>
              </span>
            ) : null}
          </div>
        ) : null}

        {executable && run && run.outputs.length > 0 ? (
          <div className={cn("px-2 pt-2", stale && "opacity-70")}>
            <CellOutputs
              outputs={run.outputs}
              tableActions={tableActions ? (output) => tableActions(cell, output) : undefined}
            />
          </div>
        ) : executable && run?.outputsPending ? (
          <p className="pt-2 text-xs text-muted-foreground">
            The last output is large; run the cell to see it again
          </p>
        ) : null}
      </div>
    </div>
  );
});
