import { memo, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Bookmark,
  Check,
  Download,
  Loader2,
  Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type {
  ChartBlock,
  CitationBlock,
  TableBlock,
} from "@/features/assistant/types";
import { ChartBlock as VegaChart } from "@/features/agents/chart-block";

/**
 * The structured blocks an answer can carry: a result grid, a chart, and the
 * sources a semantic search returned.
 *
 * The grid is deliberately more than a dump of rows. A reader asked a
 * question; the table is the evidence. Sort, filter, and copy-out are what let
 * them interrogate it without leaving the conversation. The footer states the
 * one limitation that is real (the engine's row cap), because pretending a
 * capped preview is the full result would be a lie the UI cannot back up.
 */

export const ResultTable = memo(function ResultTable({
  block,
  onSave,
  saving = false,
  saved = false,
}: {
  block: TableBlock;
  onSave?: () => void;
  saving?: boolean;
  saved?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{
    column: number;
    dir: "asc" | "desc";
  } | null>(null);

  // The tool often sends an empty title, so a blank header is the failure mode
  // to guard: an empty string is not "no title", it is an unhelpful one.
  const title = block.title?.trim() || "Query result";

  const numericColumns = useMemo(
    () =>
      block.columns.map((_, columnIndex) => {
        const values = block.rows
          .map((row) => row[columnIndex])
          .filter((cell) => cell != null);
        return values.length > 0 && values.every(numeric);
      }),
    [block.columns, block.rows],
  );

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    let out = block.rows;
    if (needle) {
      out = out.filter((row) =>
        row.some(
          (cell) =>
            cell !== null && String(cell).toLowerCase().includes(needle),
        ),
      );
    }
    if (sort) {
      const { column, dir } = sort;
      const sign = dir === "asc" ? 1 : -1;
      // Copy before sorting: the block is a prop and must not be mutated.
      out = [...out].sort((a, b) => sign * compareCells(a[column], b[column]));
    }
    return out;
  }, [block.rows, query, sort]);

  const toggleSort = (column: number) => {
    setSort((current) => {
      if (!current || current.column !== column) return { column, dir: "asc" };
      if (current.dir === "asc") return { column, dir: "desc" };
      return null;
    });
  };

  const exportCsv = () => {
    const escape = (value: unknown) => {
      const text = value === null ? "" : String(value);
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const lines = [
      block.columns.map(escape).join(","),
      ...rows.map((row) => row.map(escape).join(",")),
    ];
    const blob = new Blob([lines.join("\n")], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${slug(title)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const filtered = query.trim().length > 0;

  return (
    <div className="nova-chat-item overflow-hidden rounded-xl border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <h3 className="min-w-0 flex-1 truncate text-sm font-medium">{title}</h3>
        {onSave ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-7"
                aria-label={saved ? "Artifact saved" : "Save table as artifact"}
                disabled={saving || saved}
                onClick={onSave}
              >
                {saving ? (
                  <Loader2
                    aria-hidden="true"
                    className="size-3.5 animate-spin"
                  />
                ) : saved ? (
                  <Check aria-hidden="true" className="size-3.5" />
                ) : (
                  <Bookmark aria-hidden="true" className="size-3.5" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{saved ? "Saved" : "Save artifact"}</TooltipContent>
          </Tooltip>
        ) : null}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label="Download results as CSV"
              onClick={exportCsv}
            >
              <Download aria-hidden="true" className="size-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Download CSV</TooltipContent>
        </Tooltip>
      </div>

      <div className="flex items-center gap-2 border-b px-3 py-2">
        <Search
          aria-hidden="true"
          className="size-3.5 shrink-0 text-muted-foreground"
        />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter rows"
          aria-label="Filter rows"
          className="h-7 border-0 bg-transparent px-0 text-sm shadow-none focus-visible:ring-0"
        />
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          {rows.length}
          {filtered ? ` / ${block.rows.length}` : ""} rows
        </span>
      </div>

      <div className="max-h-96 overflow-auto">
        <table className="w-full border-separate border-spacing-0 text-sm">
          <thead className="sticky top-0 z-10 bg-muted">
            <tr>
              {block.columns.map((column, i) => (
                <th
                  key={column}
                  scope="col"
                  aria-sort={
                    sort?.column === i
                      ? sort.dir === "asc"
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                  className="border-b border-r border-border p-0 align-middle font-medium last:border-r-0"
                >
                  <button
                    type="button"
                    onClick={() => toggleSort(i)}
                    className={cn(
                      "flex w-full items-center gap-1.5 whitespace-nowrap px-3 py-2 transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring focus-visible:outline-none",
                      numericColumns[i]
                        ? "flex-row-reverse text-right"
                        : "text-left",
                    )}
                  >
                    {column}
                    {sort?.column === i && sort.dir === "desc" ? (
                      <ArrowDown aria-hidden="true" className="size-3 shrink-0" />
                    ) : (
                      <ArrowUp
                        aria-hidden="true"
                        className={cn(
                          "size-3 shrink-0",
                          sort?.column !== i && "invisible",
                        )}
                      />
                    )}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr
                key={rowIndex}
                className="even:bg-muted/20 last:[&>td]:border-b-0"
              >
                {row.map((cell, cellIndex) => (
                  <td
                    key={cellIndex}
                    className={cn(
                      "border-b border-r border-border px-3 py-2 align-middle last:border-r-0",
                      numericColumns[cellIndex]
                        ? "whitespace-nowrap text-right tabular-nums"
                        : "text-left",
                    )}
                  >
                    {cell === null ? (
                      <span className="text-muted-foreground">null</span>
                    ) : (
                      String(cell)
                    )}
                  </td>
                ))}
              </tr>
            ))}
            {!rows.length ? (
              <tr>
                <td
                  colSpan={block.columns.length}
                  className="px-3 py-6 text-center text-sm text-muted-foreground"
                >
                  {query ? `No row matches "${query}".` : "The query returned no rows."}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {block.rows.length > 0 ? (
        <p className="border-t px-3 py-2 text-xs text-muted-foreground">
          Capped at the engine's row limit for this query. Run the SQL above to
          see the full result.
        </p>
      ) : null}
    </div>
  );
});

/**
 * A number rendered right-aligned, so money and counts line up on the digit.
 * `null` is handled by the caller; a string that parses cleanly counts.
 */
function numeric(cell: string | number | null): boolean {
  if (typeof cell === "number") return true;
  if (typeof cell !== "string" || !cell.trim()) return false;
  return !Number.isNaN(Number(cell.replace(/[, _]/g, "")));
}

function compareCells(
  a: string | number | null,
  b: string | number | null,
): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  if (numeric(a) && numeric(b)) {
    return (
      Number(String(a).replace(/[, _]/g, "")) -
      Number(String(b).replace(/[, _]/g, ""))
    );
  }
  return String(a).localeCompare(String(b));
}

function slug(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "result"
  );
}

export const ResultChart = memo(function ResultChart({
  block,
  onSave,
  saving = false,
  saved = false,
}: {
  block: ChartBlock;
  onSave?: () => void;
  saving?: boolean;
  saved?: boolean;
}) {
  // The tool writes the chart's title into the spec from the user's intent.
  // The card reads it back for its header and removes it from the embedded
  // chart, so the title appears once instead of twice.
  const { title, body } = splitChartTitle(block.chart_spec);

  return (
    <div className="nova-chat-item overflow-hidden rounded-xl border bg-card">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <h3 className="min-w-0 flex-1 truncate text-sm font-medium">
          {title ?? "Chart"}
        </h3>
        {onSave ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-7"
                aria-label={saved ? "Artifact saved" : "Save chart as artifact"}
                disabled={saving || saved}
                onClick={onSave}
              >
                {saving ? (
                  <Loader2
                    aria-hidden="true"
                    className="size-3.5 animate-spin"
                  />
                ) : saved ? (
                  <Check aria-hidden="true" className="size-3.5" />
                ) : (
                  <Bookmark aria-hidden="true" className="size-3.5" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{saved ? "Saved" : "Save artifact"}</TooltipContent>
          </Tooltip>
        ) : null}
      </div>
      <div className="p-3">
        <VegaChart spec={body} />
      </div>
    </div>
  );
});

/**
 * Read a Vega-Lite spec's title for the card header, and return the spec with
 * the title removed so the chart itself does not print it a second time. A
 * spec that cannot be parsed is passed through untouched.
 */
export function splitChartTitle(spec: string): {
  title?: string;
  body: string;
} {
  try {
    const parsed = JSON.parse(spec) as Record<string, unknown>;
    const raw = parsed.title;
    const title = typeof raw === "string" && raw.trim() ? raw : undefined;
    if (!title) return { body: spec };
    delete parsed.title;
    return { title, body: JSON.stringify(parsed) };
  } catch {
    return { body: spec };
  }
}

/** The sources a semantic search cited, so an answer can be checked. */
export const CitationList = memo(function CitationList({
  citations,
}: {
  citations: CitationBlock[];
}) {
  if (!citations.length) return null;
  return (
    <div className="nova-chat-item flex flex-wrap gap-2">
      {citations.map((citation, i) => (
        <div
          key={`${citation.source ?? citation.title ?? "source"}-${i}`}
          className="max-w-xs rounded-lg border bg-muted/30 px-3 py-2"
        >
          <p className="truncate text-xs font-medium">
            {citation.title ?? citation.source}
          </p>
          {citation.snippet ? (
            <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
              {citation.snippet}
            </p>
          ) : null}
        </div>
      ))}
    </div>
  );
});
