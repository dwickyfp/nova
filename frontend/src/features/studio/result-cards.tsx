import { memo } from "react";
import { BookmarkPlus, Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ChartBlock, CitationBlock } from "@/features/assistant/types";
import { VizView } from "@/features/viz/viz-view";
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
            {onSave ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={onSave}
                disabled={saving || saved}
                aria-label={saved ? "Chart saved" : "Save chart"}
              >
                {saving ? (
                  <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
                ) : saved ? (
                  <Check aria-hidden="true" className="size-3.5" />
                ) : (
                  <BookmarkPlus aria-hidden="true" className="size-3.5" />
                )}
                {saved ? "Saved" : "Save"}
              </Button>
            ) : null}
          </div>
          <VizView presmodel={block.presmodel} />
        </div>
      </div>
    );
  }
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
