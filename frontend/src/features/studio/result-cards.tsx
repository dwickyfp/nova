import { lazy, memo, Suspense, useMemo } from "react";
import { Globe } from "lucide-react";
import type { ChartBlock, CitationBlock } from "@/features/assistant/types";
import { VizView } from "@/features/viz/viz-view";
import { AddToDatavizButton } from "./dataviz/add-to-dataviz";
import type { DatavizLocator } from "./dataviz/api";
import { ResultTable } from "./result-table";
import { cellOf, specChart } from "./runtime/components/spec-chart";

const SpecChartView = lazy(() => import("./runtime/components/spec-chart-view"));

export const ResultChart = memo(function ResultChart({
  block,
  dataviz,
}: {
  block: ChartBlock;
  /** Where the chart is stored; it offers Add to Dataviz when the server allows. */
  dataviz?: DatavizLocator;
}) {
  // A viz-core chart renders its presentation model directly; the stored
  // Vega-Lite spec remains the template for charts the core did not build.
  if (block.presmodel) {
    return (
      <div className="nova-chat-item min-w-0">
        <div className="min-w-0 rounded-xl border bg-card p-4">
          <div className="mb-2 flex min-h-7 items-center gap-2">
            <p className="min-w-0 flex-1 truncate text-sm font-medium">
              {block.presmodel.title || "Chart"}
            </p>
            <AddToDatavizButton locator={dataviz ?? null} />
          </div>
          <VizView presmodel={block.presmodel} />
        </div>
      </div>
    );
  }
  return <StoredChart spec={block.chart_spec} dataviz={dataviz} />;
});

/** An http(s) link a web citation may open; anything else renders as text. */
function webCitationHref(url: string | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

/** The sources an answer cited, so it can be checked. Web pages open in a new tab. */
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
          {citation.source === "web" ? (
            <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Globe aria-hidden className="size-3" />
              Web source
            </p>
          ) : null}
          <p className="truncate text-xs font-medium">
            {webCitationHref(citation.url) ? (
              <a
                href={webCitationHref(citation.url)!}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="underline-offset-2 hover:underline"
              >
                {citation.title ?? citation.url}
              </a>
            ) : (
              citation.title ?? citation.source
            )}
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

/** A stored Vega-Lite chart, drawn as a simple chart of its rows or shown as them. */
function StoredChart({ spec, dataviz }: { spec: string; dataviz?: DatavizLocator }) {
  const plan = useMemo(() => specChart(spec), [spec]);
  const action = <AddToDatavizButton locator={dataviz ?? null} />;
  if (!plan)
    return <p className="text-sm text-muted-foreground">This chart could not be displayed.</p>;
  if (!plan.rows.length)
    return (
      <div className="nova-chat-item min-w-0 rounded-xl border bg-card p-4">
        <div className="flex min-h-7 items-center gap-2">
          <p className="min-w-0 flex-1 truncate text-sm font-medium">{plan.title || "Chart"}</p>
          {action}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">This chart has no rows.</p>
      </div>
    );
  return (
    <div className="nova-chat-item min-w-0">
      {plan.kind === "chart" ? (
        <Suspense fallback={<div role="status" aria-label="Loading chart" className="h-64 rounded-xl bg-muted/60" />}>
          <SpecChartView plan={plan} action={action} />
        </Suspense>
      ) : (
        <ResultTable
          block={{
            title: plan.title || "Chart data",
            columns: plan.columns,
            rows: plan.rows.map((row) => plan.columns.map((column) => cellOf(row[column]))),
          }}
          action={action}
          note={null}
          emptyText="This chart has no rows."
        />
      )}
    </div>
  );
}
