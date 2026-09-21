import { Package } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ScrollArea } from "@/components/ui/scroll-area";

/**
 * Artifacts — the charts and tables an agent produces.
 *
 * v1 reads the live conversation only, so this view explains what it is and
 * where artifacts appear rather than showing a stored gallery. Persisting
 * artifacts out of a turn is a follow-on (the loop already emits `table` and
 * `chart` frames; storing them is a separate change).
 */
export function StudioArtifacts({
  onSelectAgent,
}: {
  onSelectAgent: (id: string) => void;
}) {
  void onSelectAgent;
  return (
    <>
      <header className="flex h-14 shrink-0 items-center border-b px-6">
        <h1 className="font-medium">Artifacts</h1>
      </header>
      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto w-full max-w-3xl px-6 py-10">
          <EmptyState
            icon={Package}
            title="Artifacts appear in chat"
            description="Charts and result tables are rendered inline in a conversation. Ask an agent a question that returns data and they show up there."
          />
          <div className="mt-6 flex flex-wrap justify-center gap-2">
            <Badge variant="secondary">Charts (Vega-Lite)</Badge>
            <Badge variant="secondary">Result tables</Badge>
            <Badge variant="secondary">SQL</Badge>
          </div>
        </div>
      </ScrollArea>
    </>
  );
}
