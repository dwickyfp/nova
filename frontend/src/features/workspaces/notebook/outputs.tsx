import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, Copy, Download } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/features/assistant/markdown";
import { readToken } from "@/lib/read-token";
import { cn } from "@/lib/utils";
import { ChartVisualization } from "../chart-visualization";
import type { QueryResponse } from "../types";
import { formatCell, INTERACTIVE_POLICY, needsScripts, STORAGE_SHIM } from "./helpers";
import type { CellOutput, TablePayload } from "./types";

const ORIGIN_LABEL: Record<string, string> = {
  local_duckdb: "Computed in DuckDB",
  nova: "Computed by Nova",
  python_memory: "Python data in memory",
};

function toCsv(table: TablePayload): string {
  const escape = (value: unknown) => {
    const text = value === null || value === undefined ? "" : String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [
    table.schema.map((c) => escape(c.name)).join(","),
    ...table.rows.map((row) => row.map(escape).join(",")),
  ].join("\n");
}

function rowCountLabel(table: TablePayload): string {
  const shown = table.rows.length;
  if (table.total_rows != null) {
    return table.total_rows === shown
      ? `${shown.toLocaleString("en-US")} ${shown === 1 ? "row" : "rows"}`
      : `First ${shown.toLocaleString("en-US")} of ${table.total_rows.toLocaleString("en-US")} rows`;
  }
  return table.truncated
    ? `First ${shown.toLocaleString("en-US")} rows, total not counted`
    : `${shown.toLocaleString("en-US")} rows`;
}

const TableOutput = memo(function TableOutput({
  table,
  name,
  actions,
}: {
  table: TablePayload;
  name?: string | null;
  actions?: React.ReactNode;
}) {
  const [view, setView] = useState<"table" | "chart">("table");
  const chartInput = useMemo(
    () =>
      ({
        success: true,
        columns: table.schema.map((c) => c.name),
        rows: table.rows,
        row_count: table.rows.length,
        affected_rows: 0,
        elapsed_ms: 0,
        original_sql: "",
        executed_sql: "",
        warnings: [],
      }) satisfies QueryResponse,
    [table],
  );

  function download() {
    const blob = new Blob([toCsv(table)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${name || "result"}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="rounded-md border border-surface-border bg-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-surface-border px-3 py-2">
        <div
          role="tablist"
          aria-label="Result view"
          className="flex rounded-md bg-muted p-0.5"
        >
          {(["table", "chart"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              role="tab"
              aria-selected={view === mode}
              onClick={() => setView(mode)}
              className={cn(
                "rounded px-2.5 py-1 text-xs transition-colors",
                view === mode
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {mode === "table" ? "Table" : "Chart"}
            </button>
          ))}
        </div>
        {name ? <span className="font-mono text-xs text-foreground">{name}</span> : null}
        <span className="text-xs text-muted-foreground tabular-nums">
          {rowCountLabel(table)}
        </span>
        <span className="text-xs text-muted-foreground">
          {ORIGIN_LABEL[table.origin] ?? "Result"}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {actions}
          <Button
            size="icon"
            variant="ghost"
            className="size-7 text-muted-foreground"
            aria-label="Copy rows as CSV"
            title="Copy rows as CSV"
            onClick={() => {
              void navigator.clipboard.writeText(toCsv(table));
              toast.success("Rows copied");
            }}
          >
            <Copy className="size-3.5" />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="size-7 text-muted-foreground"
            aria-label="Download rows as CSV"
            title="Download rows as CSV"
            onClick={download}
          >
            <Download className="size-3.5" />
          </Button>
        </div>
      </div>
      {view === "table" ? (
        <div className="max-h-96 overflow-auto">
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-card">
              <tr>
                {table.schema.map((column) => (
                  <th
                    key={column.name}
                    scope="col"
                    className="border-b border-surface-border px-3 py-2 text-left align-bottom font-medium whitespace-nowrap"
                  >
                    <span className="block text-foreground">{column.name}</span>
                    <span className="block font-mono text-xs font-normal text-muted-foreground">
                      {column.type.toLowerCase()}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="viz-numerals">
              {table.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="hover:bg-surface-1">
                  {row.map((value, columnIndex) => (
                    <td
                      key={columnIndex}
                      className={cn(
                        "max-w-80 truncate border-b border-surface-border px-3 py-1.5 whitespace-nowrap",
                        typeof value === "number" && "text-right tabular-nums",
                        value === null && "text-muted-foreground",
                      )}
                      title={value === null ? "null" : String(value)}
                    >
                      {formatCell(value)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {table.rows.length === 0 ? (
            <p className="px-3 py-4 text-sm text-muted-foreground">
              The query ran and returned no rows
            </p>
          ) : null}
        </div>
      ) : (
        <div className="h-96">
          <ChartVisualization queryResult={chartInput} builderInitiallyCollapsed />
        </div>
      )}
      {table.truncated && view === "chart" ? (
        <p className="border-t border-surface-border px-3 py-1.5 text-xs text-muted-foreground">
          The chart uses the rows shown in the table
        </p>
      ) : null}
    </div>
  );
});

function ErrorOutput({ output }: { output: Extract<CellOutput, { type: "error" }> }) {
  const [open, setOpen] = useState(false);
  const hasTrace = output.traceback.length > 0;
  return (
    <div className="rounded-md border border-destructive/25 bg-destructive/5 px-3 py-2">
      <p className="font-mono text-sm break-words text-destructive">
        {output.ename}
        {output.evalue ? `: ${output.evalue}` : ""}
      </p>
      {hasTrace ? (
        <>
          <button
            type="button"
            className="mt-1 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
            Traceback
          </button>
          {open ? (
            <pre className="mt-2 max-h-80 overflow-auto font-mono text-xs leading-5 whitespace-pre text-foreground">
              {output.traceback.join("\n")}
            </pre>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/**
 * HTML and SVG produced by notebook code. It never runs in Nova's page: it is
 * shown in a sandboxed frame with an opaque origin, under a policy that loads
 * nothing from the network. Markup without scripts gets no script permission
 * at all; interactive output (PyGWalker, plotly) may run its own inline
 * scripts, still without Nova's origin, cookies or any network access.
 */
function SandboxedMarkup({ markup, title }: { markup: string; title: string }) {
  const foreground = readToken("--foreground", "#1e293b");
  const background = readToken("--card", "#ffffff");
  const border = readToken("--border", "#e0e5ec");
  const interactive = needsScripts(markup);
  const frame = useRef<HTMLIFrameElement | null>(null);
  const [height, setHeight] = useState<number | null>(null);

  useEffect(() => {
    if (!interactive) return;
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return;
      const value = (event.data as { novaFrameHeight?: unknown } | null)?.novaFrameHeight;
      if (typeof value === "number" && Number.isFinite(value)) {
        setHeight(Math.max(160, Math.min(900, Math.ceil(value))));
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [interactive]);

  const policy = interactive
    ? INTERACTIVE_POLICY
    : "default-src 'none'; img-src data:; style-src 'unsafe-inline'";
  const sizing = interactive
    ? "<script>(function(){var send=function(){parent.postMessage({novaFrameHeight:" +
      "document.documentElement.scrollHeight},'*')};new ResizeObserver(send)" +
      ".observe(document.documentElement);window.addEventListener('load',send)})()</script>"
    : "";
  const shim = interactive ? `<script>${STORAGE_SHIM}</script>` : "";
  const page = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}">${shim}<style>body{margin:8px;font:13px/1.45 system-ui,sans-serif;color:${foreground};background:${background}}table{border-collapse:collapse}td,th{border:1px solid ${border};padding:2px 6px}</style></head><body>${markup}${sizing}</body></html>`;
  return (
    <iframe
      ref={frame}
      title={title}
      sandbox={interactive ? "allow-scripts allow-downloads" : ""}
      srcDoc={page}
      referrerPolicy="no-referrer"
      style={interactive ? { height: height ?? 480 } : undefined}
      className={cn(
        "w-full resize-y rounded-md border border-surface-border bg-card",
        !interactive && "h-64",
      )}
    />
  );
}

export function CellOutputs({
  outputs,
  tableActions,
}: {
  outputs: CellOutput[];
  tableActions?: (output: Extract<CellOutput, { type: "table" }>) => React.ReactNode;
}) {
  if (!outputs.length) return null;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      {outputs.map((output, index) => {
        switch (output.type) {
          case "stream":
            return (
              <pre
                key={index}
                className={cn(
                  "max-h-96 overflow-auto font-mono text-xs leading-5 break-words whitespace-pre-wrap",
                  output.name === "stderr" ? "text-warning-strong" : "text-foreground",
                )}
              >
                {output.text}
              </pre>
            );
          case "text":
            return (
              <pre
                key={index}
                className="max-h-96 overflow-auto font-mono text-xs leading-5 break-words whitespace-pre-wrap text-foreground"
              >
                {output.text}
              </pre>
            );
          case "status":
            return (
              <p key={index} className="text-sm text-muted-foreground">
                {output.text}
              </p>
            );
          case "truncated":
            return (
              <p key={index} className="text-xs text-warning-strong">
                {output.text}
              </p>
            );
          case "error":
            return <ErrorOutput key={index} output={output} />;
          case "table":
            return (
              <TableOutput
                key={index}
                table={output.table}
                name={output.name}
                actions={tableActions?.(output)}
              />
            );
          case "image":
            return (
              <img
                key={index}
                alt="Cell output"
                src={`data:${output.mime === "image/jpeg" ? "image/jpeg" : "image/png"};base64,${output.data}`}
                className="max-h-[480px] max-w-full self-start rounded-md border border-surface-border bg-white"
              />
            );
          case "svg":
            return <SandboxedMarkup key={index} markup={output.svg} title="SVG output" />;
          case "html":
            return <SandboxedMarkup key={index} markup={output.html} title="HTML output" />;
          case "markdown":
            return (
              <div key={index} className="text-sm">
                <Markdown offline>{output.text}</Markdown>
              </div>
            );
          default:
            return null;
        }
      })}
    </div>
  );
}
