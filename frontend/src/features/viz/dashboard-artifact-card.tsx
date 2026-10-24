import { LayoutDashboard } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";
import type { DashboardArtifactBlock } from "./types";

/**
 * The chat card for a dashboard the agent just composed: what it holds and
 * where to open it live. It carries no data of its own; the dashboard view
 * re-runs every sheet under the reader's own access.
 */
export function DashboardArtifactCard({ block }: { block: DashboardArtifactBlock }) {
  const navigate = useNavigate();
  return (
    <div className="nova-chat-item min-w-0">
      <div className="min-w-0 rounded-xl border bg-card p-4">
        <div className="flex items-start gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
            <LayoutDashboard aria-hidden="true" className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold">{block.title}</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {block.charts.length} chart{block.charts.length === 1 ? "" : "s"}
              {block.charts.length
                ? `: ${block.charts.map((chart) => chart.title).join(", ")}`
                : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={() =>
              navigate({
                to: "/studio",
                search: { view: "artifacts", item: block.artifact_id },
              })
            }
            className="rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
          >
            Open dashboard
          </button>
        </div>
      </div>
    </div>
  );
}
