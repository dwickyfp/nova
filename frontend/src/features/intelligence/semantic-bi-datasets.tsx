import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { semanticViewsApi, type SemanticBiDataset } from "./semantic-views-api";

const STATUS: Record<string, { label: string; tone: "success" | "danger" | "neutral" }> = {
  SYNCED: { label: "In BI", tone: "success" },
  FAILED: { label: "Not synced", tone: "danger" },
};

/**
 * The BI datasets a published view keeps in sync (docs/arch-17-superset.md,
 * Phase 5). Publishing syncs them; the owner can sync again after a failure.
 */
export function SemanticBiDatasets({ viewId, published, isPrivate, canSync }: {
  viewId: string;
  published: boolean;
  isPrivate: boolean;
  canSync: boolean;
}) {
  const queryClient = useQueryClient();
  const epoch = useAuthStore((state) => state.securityEpoch);
  const queryKey = ["intelligence", "semantic", viewId, "bi-datasets", epoch];
  const datasets = useQuery({
    queryKey,
    queryFn: () => semanticViewsApi.biDatasets(viewId),
  });
  const sync = useMutation({
    mutationFn: () => semanticViewsApi.syncBi(viewId),
    onSuccess: (result) => queryClient.setQueryData(queryKey, result),
  });
  const items: SemanticBiDataset[] = datasets.data?.datasets ?? [];

  return (
    <section
      aria-labelledby="semantic-view-bi"
      className="min-w-0 rounded-lg border bg-background p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 id="semantic-view-bi" className="font-medium">BI datasets</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Each fact table becomes a BI dataset with this view&apos;s measures,
            so charts in BI use the same definitions as agents.
          </p>
        </div>
        {canSync && published && !isPrivate ? (
          <Button
            variant="outline"
            size="sm"
            disabled={sync.isPending}
            onClick={() => sync.mutate()}
          >
            {sync.isPending ? "Syncing…" : items.length ? "Sync again" : "Sync to BI"}
          </Button>
        ) : null}
      </div>
      {datasets.isPending ? (
        <p className="mt-3 text-sm text-muted-foreground" role="status">Loading BI datasets…</p>
      ) : datasets.isError ? (
        <p role="alert" className="mt-3 text-sm text-destructive">BI datasets could not be loaded.</p>
      ) : items.length ? (
        <ul className="mt-4 divide-y rounded-lg border">
          {items.map((item) => {
            const status = STATUS[item.status] ?? { label: item.status, tone: "neutral" as const };
            return (
              <li key={item.dataset} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-sm">
                <span className="min-w-0 break-all font-medium">{item.dataset}</span>
                <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
                {item.version ? <span className="text-muted-foreground">v{item.version}</span> : null}
                {item.bi_path ? (
                  <Link
                    to="/analytics"
                    search={{ path: item.bi_path }}
                    className="ms-auto text-sm font-medium text-primary underline-offset-4 hover:underline"
                  >
                    Open in BI
                  </Link>
                ) : null}
                {item.message ? (
                  <p className="basis-full text-xs text-muted-foreground">{item.message}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">
          {isPrivate
            ? "Private views stay out of BI."
            : published
              ? "Not in BI yet."
              : "Publish a version to make its datasets available in BI."}
        </p>
      )}
      {sync.isError ? (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {sync.error instanceof Error ? sync.error.message : "The view could not be synced to BI."}
        </p>
      ) : null}
    </section>
  );
}
