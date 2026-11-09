import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ProposedRewrite } from "@/features/assistant/query-attach";
import { rewriteBlocks } from "./rewrite-blocks";

/**
 * Neva's suggested SQL, floating over the editor as one unified diff: the
 * current lines on top, the suggested lines below them.
 */
export function RewriteReview({
  rewrite,
  onCancel,
  onApply,
  onApplyAndRun,
}: {
  rewrite: ProposedRewrite;
  onCancel: () => void;
  onApply: () => void;
  onApplyAndRun: () => void;
}) {
  const blocks = rewriteBlocks(rewrite);
  const rows = blocks.flatMap((block) => block.rows);
  const added = rows.filter((row) => row.kind === "added").length;
  const removed = rows.filter((row) => row.kind === "removed").length;

  return (
    <section
      aria-label="Suggested change from Neva"
      className="pointer-events-auto flex max-h-[min(60%,26rem)] flex-col overflow-hidden rounded-xl bg-background/95 supports-[backdrop-filter]:bg-background/75 shadow-[0_12px_40px_-16px_rgb(0_0_0/0.35)] backdrop-blur-xl backdrop-saturate-150"
    >
      <header className="flex items-center gap-3 px-4 pt-3 pb-2">
        <p className="text-sm font-medium">Suggested change</p>
        <p className="text-xs text-muted-foreground">from Neva</p>
        <p className="ml-auto font-mono text-xs tabular-nums">
          <span className={added ? "text-success-strong" : "text-muted-foreground"}>
            +{added}
          </span>{" "}
          <span className={removed ? "text-destructive" : "text-muted-foreground"}>
            −{removed}
          </span>
        </p>
      </header>

      <div className="min-h-0 flex-1 overflow-auto px-2">
        {blocks.map((block) => (
          <div key={block.key} className="py-1">
            {blocks.length > 1 ? (
              <p className="px-2 pb-1 font-mono text-xs text-muted-foreground">
                Line {block.line}
              </p>
            ) : null}
            <ol className="overflow-x-auto rounded-lg font-mono text-xs leading-5">
              {block.rows.map((row, index) => (
                <li
                  key={`${row.kind}-${row.old ?? ""}-${row.new ?? ""}-${index}`}
                  className={cn(
                    "grid w-max min-w-full grid-cols-[2.25rem_2.25rem_1rem_auto]",
                    row.kind === "removed" && "bg-destructive/10",
                    row.kind === "added" && "bg-success/15",
                    row.kind === "context" && "text-muted-foreground",
                  )}
                >
                  <span
                    aria-hidden="true"
                    className="select-none pr-2 text-right text-muted-foreground tabular-nums"
                  >
                    {row.old}
                  </span>
                  <span
                    aria-hidden="true"
                    className="select-none pr-2 text-right text-muted-foreground tabular-nums"
                  >
                    {row.new}
                  </span>
                  <span
                    aria-hidden="true"
                    className={cn(
                      "select-none",
                      row.kind === "removed" && "text-destructive",
                      row.kind === "added" && "text-success-strong",
                    )}
                  >
                    {row.kind === "removed" ? "−" : row.kind === "added" ? "+" : ""}
                  </span>
                  <span className="whitespace-pre pr-3">
                    {row.kind === "context" ? null : (
                      <span className="sr-only">
                        {row.kind === "removed" ? "Removed: " : "Added: "}
                      </span>
                    )}
                    {row.text || " "}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>

      <footer className="flex items-center justify-end gap-2 px-4 pt-2 pb-3">
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="button" size="sm" variant="secondary" onClick={onApply}>
          Apply
        </Button>
        <Button type="button" size="sm" onClick={onApplyAndRun}>
          Apply &amp; Run
        </Button>
      </footer>
    </section>
  );
}
