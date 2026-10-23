import { memo } from "react";
import type { ChartBlock, CitationBlock } from "@/features/assistant/types";
import { UiChart } from "./intelligent-ui/ui-chart";

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
  // A tool chart is drawn by the same interactive components as every other
  // Studio visual; its stored Vega-Lite spec is only the data template.
  return (
    <div className="nova-chat-item min-w-0">
      <UiChart spec={block.chart_spec} onSave={onSave} saving={saving} saved={saved} />
    </div>
  );
});

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
