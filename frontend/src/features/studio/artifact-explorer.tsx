import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowDown, ArrowUp, BarChart3, LayoutDashboard, Loader2, MoreHorizontal, PackageOpen, Plus, Search, Star } from "lucide-react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { studioApi, type StudioArtifact } from "@/features/agents/api";
import { describeView, sourceOf } from "@/features/viz/insight";
import type { DashboardSpec, PresModel } from "@/features/viz/types";
import { VizView } from "@/features/viz/viz-view";
import { cn } from "@/lib/utils";
import { LegacyNotice } from "./legacy-conversion";

/**
 * ArtifactExplorer is the Studio content browser. Dashboards show as cards
 * whose thumbnails draw every zone with its sheet's marks; sheets show as a
 * table that reads each view's current answer from its own data, with its
 * shape, source and last change. Filtered by kind, searchable, sortable, with
 * favorites, and actions to open, edit, refresh, delete, or create.
 */

type Kind = "all" | "dashboard" | "chart" | "favorites";
type SortKey = "updated" | "created" | "title";

const FAVORITES_KEY = "nova.studio.favorite-artifacts";

function readStored<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeStored(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Favorites are a convenience; nothing depends on them.
  }
}

function toDate(value: string): Date {
  return new Date(value.includes("T") || value.endsWith("Z") ? value : `${value.replace(" ", "T")}Z`);
}

/** "24 Oct, 09:25" in the viewer's locale and time zone. */
function formatModified(value: string): string {
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.toLocaleDateString(undefined, { day: "numeric", month: "short" })}, ${date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
}

export function ArtifactExplorer({
  onOpen,
}: {
  onOpen: (artifactId: string, options?: { edit?: boolean }) => void;
}) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<Kind>("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; direction: "asc" | "desc" }>({
    key: "updated",
    direction: "desc",
  });
  const [favorites, setFavorites] = useState<string[]>(() => readStored<string[]>(FAVORITES_KEY, []));
  const [creating, setCreating] = useState<"chart" | "dashboard" | null>(null);
  const [deleting, setDeleting] = useState<StudioArtifact | null>(null);

  const artifactsQuery = useQuery({
    queryKey: ["studio", "artifacts"],
    queryFn: () => studioApi.listArtifacts(),
  });

  const toggleFavorite = (id: string) => {
    setFavorites((current) => {
      const next = current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
      writeStored(FAVORITES_KEY, next);
      return next;
    });
  };

  const remove = useMutation({
    mutationFn: (id: string) => studioApi.deleteArtifact(id),
    onSuccess: () => {
      toast.success("Deleted");
      void queryClient.invalidateQueries({ queryKey: ["studio", "artifacts"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const all = useMemo(() => artifactsQuery.data?.artifacts ?? [], [artifactsQuery.data]);
  const counts: Record<Kind, number> = {
    all: all.length,
    dashboard: all.filter((item) => item.artifact_type === "dashboard").length,
    chart: all.filter((item) => item.artifact_type === "chart").length,
    favorites: all.filter((item) => favorites.includes(item.artifact_id)).length,
  };
  const legacyCount = all.filter((item) => item.legacy).length;
  const latest = all.reduce<string | null>((max, item) => (!max || item.updated_at > max ? item.updated_at : max), null);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const rows = all.filter((item) => {
      if (kind === "favorites" && !favorites.includes(item.artifact_id)) return false;
      if ((kind === "dashboard" || kind === "chart") && item.artifact_type !== kind) return false;
      return !needle || item.title.toLowerCase().includes(needle);
    });
    const value = (item: StudioArtifact): string =>
      sort.key === "title" ? item.title.toLowerCase() : sort.key === "created" ? item.created_at : item.updated_at;
    rows.sort((a, b) => {
      const order = value(a) < value(b) ? -1 : value(a) > value(b) ? 1 : 0;
      return sort.direction === "asc" ? order : -order;
    });
    return rows;
  }, [all, favorites, kind, search, sort]);
  const dashboards = visible.filter((item) => item.artifact_type === "dashboard");
  const sheets = visible.filter((item) => item.artifact_type === "chart");

  const tabs: { value: Kind; label: string }[] = [
    { value: "all", label: "All" },
    { value: "dashboard", label: "Dashboards" },
    { value: "chart", label: "Sheets" },
    { value: "favorites", label: "Favorites" },
  ];

  return (
    <div className="viz-numerals flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b px-5 pt-6 sm:px-8">
        <div className="flex flex-wrap items-end gap-4">
          <div className="min-w-0 flex-1 basis-80">
            <h1 className="type-display">Explore</h1>
            <p className="type-ui mt-2 text-ink-secondary">
              {counts.all} {counts.all === 1 ? "view" : "views"}
              {latest ? (
                <>
                  <span aria-hidden="true" className="mx-1.5 text-muted-foreground">·</span>
                  latest change {formatModified(latest)}
                </>
              ) : null}
            </p>
          </div>
          <label className="relative flex w-full items-center sm:w-72">
            <span className="sr-only">Search views</span>
            <Search aria-hidden="true" className="absolute left-2.5 size-4 text-muted-foreground" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search views"
              className="type-ui h-9 pl-8"
            />
          </label>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" className="h-9 gap-1.5">
                <Plus aria-hidden="true" className="size-4" /> New view
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => setCreating("chart")}>
                <BarChart3 aria-hidden="true" className="size-4" /> Sheet from a query
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setCreating("dashboard")}>
                <LayoutDashboard aria-hidden="true" className="size-4" /> Dashboard
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <div className="-mb-px mt-5 flex flex-wrap items-end gap-3">
          <nav className="flex min-w-0 flex-1 gap-1 overflow-x-auto [scrollbar-width:none]" aria-label="Content type">
            {tabs.map((tab) => (
              <button
                key={tab.value}
                type="button"
                onClick={() => setKind(tab.value)}
                aria-current={kind === tab.value ? "page" : undefined}
                className={cn(
                  "type-ui shrink-0 border-b-2 px-3 py-2 transition-colors focus-visible:outline-2 focus-visible:outline-ring",
                  kind === tab.value
                    ? "border-primary font-semibold text-foreground"
                    : "border-transparent text-ink-secondary hover:text-foreground",
                )}
              >
                {tab.label}
                <span className="type-meta ml-1.5">{counts[tab.value]}</span>
              </button>
            ))}
          </nav>
          <div className="flex items-center gap-1.5 pb-1.5">
            <Select value={sort.key} onValueChange={(value) => setSort((current) => ({ ...current, key: value as SortKey }))}>
              <SelectTrigger aria-label="Sort by" className="type-ui h-8 w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="updated">Last changed</SelectItem>
                <SelectItem value="created">Date created</SelectItem>
                <SelectItem value="title">Name</SelectItem>
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8"
              aria-label={sort.direction === "asc" ? "Sort descending" : "Sort ascending"}
              onClick={() => setSort((current) => ({ ...current, direction: current.direction === "asc" ? "desc" : "asc" }))}
            >
              {sort.direction === "asc" ? <ArrowUp aria-hidden="true" className="size-4" /> : <ArrowDown aria-hidden="true" className="size-4" />}
            </Button>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-7 sm:px-8">
        {artifactsQuery.isLoading ? (
          <p className="type-ui flex items-center gap-2 py-10 text-ink-secondary" role="status">
            <Loader2 aria-hidden="true" className="size-4 animate-spin" /> Loading views
          </p>
        ) : artifactsQuery.isError ? (
          <EmptyState
            variant="error"
            icon={AlertCircle}
            title="Views could not be loaded"
            description={(artifactsQuery.error as Error).message}
            action={<Button variant="outline" size="sm" onClick={() => void artifactsQuery.refetch()}>Try again</Button>}
          />
        ) : !all.length ? (
          <EmptyState
            icon={PackageOpen}
            title="Nothing saved yet"
            description="Save a chart from a conversation, or create a sheet from a query. Nova keeps the query and recomputes the view from current data."
            action={<Button size="sm" onClick={() => setCreating("chart")}>New sheet</Button>}
          />
        ) : !visible.length ? (
          <EmptyState
            icon={kind === "favorites" ? Star : Search}
            title={kind === "favorites" && !search.trim() ? "No favorites yet" : "Nothing matches"}
            description={
              kind === "favorites" && !search.trim()
                ? "Star a dashboard or sheet to keep it here."
                : `No ${kind === "all" ? "view" : tabs.find((tab) => tab.value === kind)?.label.toLowerCase()} matches “${search.trim()}”.`
            }
          />
        ) : (
          <div className="flex flex-col gap-10">
            {legacyCount ? <LegacyNotice count={legacyCount} /> : null}
            {dashboards.length ? (
              <section aria-labelledby="explore-dashboards">
                <h2 id="explore-dashboards" className="type-title mb-3">
                  Dashboards<span className="type-meta ml-2 font-normal">{dashboards.length}</span>
                </h2>
                <ul className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,26rem),1fr))] gap-5">
                  {dashboards.map((artifact) => (
                    <li key={artifact.artifact_id}>
                      <DashboardCard
                        artifact={artifact}
                        favorite={favorites.includes(artifact.artifact_id)}
                        onToggleFavorite={() => toggleFavorite(artifact.artifact_id)}
                        onOpen={(edit) => onOpen(artifact.artifact_id, { edit })}
                        onDelete={() => setDeleting(artifact)}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {sheets.length ? (
              <section aria-labelledby="explore-sheets">
                <h2 id="explore-sheets" className="type-title mb-3">
                  Sheets<span className="type-meta ml-2 font-normal">{sheets.length}</span>
                </h2>
                <div className="overflow-x-auto rounded-lg border">
                  <table className="w-full min-w-[56rem] border-collapse">
                    <thead className="bg-surface-1">
                      <tr className="text-left">
                        <th scope="col" className="type-label w-10 border-b px-3 py-2.5"><span className="sr-only">Favorite</span></th>
                        <th scope="col" className="type-label border-b px-3 py-2.5 text-ink-secondary">View</th>
                        <th scope="col" className="type-label w-36 border-b px-3 py-2.5 text-ink-secondary">Shape</th>
                        <th scope="col" className="type-label border-b px-3 py-2.5 text-ink-secondary">Current answer</th>
                        <th scope="col" className="type-label w-52 border-b px-3 py-2.5 text-ink-secondary">Source</th>
                        <th scope="col" className="type-label w-32 border-b px-3 py-2.5 text-ink-secondary">Updated</th>
                        <th scope="col" className="w-10 border-b px-3 py-2.5"><span className="sr-only">Actions</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {sheets.map((artifact) => (
                        <SheetRow
                          key={artifact.artifact_id}
                          artifact={artifact}
                          favorite={favorites.includes(artifact.artifact_id)}
                          onToggleFavorite={() => toggleFavorite(artifact.artifact_id)}
                          onOpen={(edit) => onOpen(artifact.artifact_id, { edit })}
                          onDelete={() => setDeleting(artifact)}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}
          </div>
        )}
      </div>

      <CreateDialog
        kind={creating}
        onOpenChange={(open) => !open && setCreating(null)}
        onCreated={(artifact) => {
          void queryClient.invalidateQueries({ queryKey: ["studio", "artifacts"] });
          onOpen(artifact.artifact_id, { edit: true });
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={`Delete ${deleting?.title ?? "this view"}?`}
        desc={
          deleting?.artifact_type === "chart"
            ? "The sheet and its saved query are removed. Dashboards that show it lose that zone."
            : "The dashboard layout is removed. Its sheets stay in Explore."
        }
        confirmText="Delete"
        destructive
        handleConfirm={() => {
          if (deleting) remove.mutate(deleting.artifact_id);
          setDeleting(null);
        }}
      />
    </div>
  );
}

function FavoriteButton({ favorite, title, onToggle }: { favorite: boolean; title: string; onToggle: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={favorite}
      aria-label={favorite ? `Remove ${title} from favorites` : `Add ${title} to favorites`}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
      className="rounded p-1 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
    >
      <Star aria-hidden="true" className={cn("size-4", favorite && "fill-current text-primary")} />
    </button>
  );
}

function ItemMenu({
  artifact,
  onOpen,
  onRefresh,
  onDelete,
}: {
  artifact: StudioArtifact;
  onOpen: (edit: boolean) => void;
  onRefresh?: () => void;
  onDelete: () => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Actions for ${artifact.title}`}
          onClick={(event) => event.stopPropagation()}
          className="rounded p-1 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        >
          <MoreHorizontal aria-hidden="true" className="size-4" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" onClick={(event) => event.stopPropagation()}>
        <DropdownMenuItem onSelect={() => onOpen(false)}>Open</DropdownMenuItem>
        {artifact.legacy ? null : <DropdownMenuItem onSelect={() => onOpen(true)}>Edit in BI</DropdownMenuItem>}
        {onRefresh ? <DropdownMenuItem onSelect={onRefresh}>Recompute</DropdownMenuItem> : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={onDelete}>
          Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function DashboardCard({
  artifact,
  favorite,
  onToggleFavorite,
  onOpen,
  onDelete,
}: {
  artifact: StudioArtifact;
  favorite: boolean;
  onToggleFavorite: () => void;
  onOpen: (edit: boolean) => void;
  onDelete: () => void;
}) {
  const detail = useQuery({
    queryKey: ["studio", "artifact", artifact.artifact_id],
    queryFn: () => studioApi.getArtifact(artifact.artifact_id),
    staleTime: 60_000,
  });
  const spec = detail.data?.viz_spec as unknown as DashboardSpec | null;
  const sheetCount = (spec?.zones ?? []).filter((zone) => zone.kind === "sheet").length;
  return (
    <article className="group flex h-full min-w-0 flex-col overflow-hidden rounded-lg border bg-card transition-colors focus-within:ring-2 focus-within:ring-ring hover:border-viz-axis">
      <button
        type="button"
        onClick={() => onOpen(false)}
        aria-label={`Open ${artifact.title}`}
        className="relative block h-48 w-full overflow-hidden border-b bg-surface-1 text-left focus-visible:outline-none"
      >
        {artifact.bi_view_path ? (
          // Superset thumbnails need background rendering (Phase 2 of
          // docs/arch-17-superset.md); until then a BI dashboard shows its kind.
          <span className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
            <LayoutDashboard aria-hidden="true" className="size-6" />
            <span className="type-meta">Opens in BI</span>
          </span>
        ) : (
          <DashboardThumbnail artifactId={artifact.artifact_id} spec={spec} loading={detail.isLoading} updatedAt={detail.data?.updated_at} />
        )}
      </button>
      <div className="flex items-start gap-2 px-4 py-3">
        <div className="min-w-0 flex-1">
          <h3 className="type-title truncate" title={artifact.title}>
            <button type="button" onClick={() => onOpen(false)} className="max-w-full truncate text-left hover:underline focus-visible:outline-none">
              {artifact.title}
            </button>
          </h3>
          <p className="type-meta mt-1 truncate">
            {artifact.bi_view_path
              ? "BI dashboard"
              : detail.isLoading
                ? "Dashboard"
                : `${sheetCount} ${sheetCount === 1 ? "sheet" : "sheets"}`}
            <span aria-hidden="true" className="mx-1.5">·</span>
            {formatModified(artifact.updated_at)}
          </p>
        </div>
        <FavoriteButton favorite={favorite} title={artifact.title} onToggle={onToggleFavorite} />
        <ItemMenu artifact={artifact} onOpen={onOpen} onDelete={onDelete} />
      </div>
    </article>
  );
}

/** A dashboard thumbnail: its zones, each drawing the sheet's marks. */
function DashboardThumbnail({
  artifactId,
  spec,
  loading,
  updatedAt,
}: {
  artifactId: string;
  spec: DashboardSpec | null;
  loading: boolean;
  updatedAt?: string;
}) {
  const compute = useQuery({
    queryKey: ["studio", "dashboard-thumbnail", artifactId, updatedAt],
    queryFn: () => studioApi.computeVizDashboard({ spec: spec as unknown as Record<string, unknown> }),
    enabled: Boolean(spec?.zones?.length),
    staleTime: 60_000,
  });
  if (loading) {
    return (
      <span className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 aria-hidden="true" className="size-4 animate-spin" />
        <span className="sr-only">Loading the layout</span>
      </span>
    );
  }
  const zones = spec?.zones ?? [];
  if (!zones.length) return <span className="type-meta flex h-full items-center justify-center">No sheets placed yet</span>;
  const rows = Math.max(1, ...zones.map((zone) => zone.rect.y + zone.rect.h));
  const results = new Map((compute.data?.zones ?? []).map((zone) => [zone.zone_id, zone]));
  return (
    <div
      className="pointer-events-none grid h-full gap-1.5 p-1.5"
      style={{ gridTemplateColumns: "repeat(24, minmax(0, 1fr))", gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}
      aria-hidden="true"
    >
      {zones.map((zone) => {
        const result = results.get(zone.zone_id);
        return (
          <div
            key={zone.zone_id}
            className="flex min-h-0 min-w-0 flex-col gap-1 overflow-hidden rounded border bg-card p-2"
            style={{ gridColumn: `${zone.rect.x + 1} / span ${zone.rect.w}`, gridRow: `${zone.rect.y + 1} / span ${zone.rect.h}` }}
          >
            {zone.kind === "sheet" ? (
              <>
                <span className="type-meta truncate">{result?.title ?? ""}</span>
                <div className="min-h-0 flex-1">
                  {result?.presmodel ? (
                    <VizView presmodel={result.presmodel} thumbnail />
                  ) : result?.error ? (
                    <span className="type-meta flex h-full items-center gap-1">
                      <AlertCircle aria-hidden="true" className="size-3 shrink-0" />
                      <span className="line-clamp-2">{result.error}</span>
                    </span>
                  ) : compute.isLoading ? (
                    <span className="block h-full rounded bg-muted" />
                  ) : null}
                </div>
              </>
            ) : zone.kind === "text" ? (
              <span className="type-label truncate">{zone.text?.content}</span>
            ) : zone.kind === "filter" ? (
              <span className="type-meta truncate">Filter: {zone.filter?.field}</span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function SheetRow({
  artifact,
  favorite,
  onToggleFavorite,
  onOpen,
  onDelete,
}: {
  artifact: StudioArtifact;
  favorite: boolean;
  onToggleFavorite: () => void;
  onOpen: (edit: boolean) => void;
  onDelete: () => void;
}) {
  const sheet = useQuery({
    queryKey: ["studio", "artifact-data", artifact.artifact_id],
    queryFn: () => studioApi.refreshArtifact(artifact.artifact_id),
    staleTime: 60_000,
  });
  const presmodel = (sheet.data?.presmodel as PresModel | undefined) ?? null;
  const insight = presmodel ? describeView(presmodel) : null;
  const source = sourceOf(sheet.data?.artifact.sql_text);
  return (
    <tr className="border-b border-viz-grid last:border-b-0 hover:bg-surface-1">
      <td className="px-3 py-2">
        <FavoriteButton favorite={favorite} title={artifact.title} onToggle={onToggleFavorite} />
      </td>
      <td className="type-ui max-w-72 px-3 py-2.5">
        <button
          type="button"
          onClick={() => onOpen(false)}
          className="block max-w-full truncate text-left font-semibold text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring"
          title={artifact.title}
        >
          {artifact.title}
        </button>
      </td>
      <td className="px-3 py-1.5">
        <button
          type="button"
          onClick={() => onOpen(false)}
          aria-label={`Open ${artifact.title}`}
          className="block h-8 w-28 overflow-hidden rounded focus-visible:outline-2 focus-visible:outline-ring"
        >
          {presmodel ? (
            <span className="pointer-events-none block h-full">
              <VizView presmodel={presmodel} thumbnail />
            </span>
          ) : sheet.isLoading ? (
            <span className="block h-full rounded bg-muted" />
          ) : null}
        </button>
      </td>
      <td className="type-ui px-3 py-2.5 text-ink-secondary">
        {sheet.isLoading ? (
          <span className="type-meta" role="status">Computing</span>
        ) : sheet.isError ? (
          <span className="type-meta inline-flex items-center gap-1">
            <AlertCircle aria-hidden="true" className="size-3.5" /> Data unavailable under your access
          </span>
        ) : insight ? (
          <>
            <span className="font-semibold text-foreground">
              {insight.lead.subject} {insight.lead.verb} {insight.lead.num}
              {insight.lead.unit}
            </span>
          </>
        ) : (
          <span className="type-meta">{sheet.data ? `${sheet.data.row_count.toLocaleString()} rows` : ""}</span>
        )}
      </td>
      <td className="px-3 py-2.5">
        {source ? <span className="type-code block truncate text-ink-secondary" title={source}>{source}</span> : <span className="type-meta">Query</span>}
      </td>
      <td className="type-meta px-3 py-2.5 whitespace-nowrap">{formatModified(artifact.updated_at)}</td>
      <td className="px-3 py-2">
        <ItemMenu artifact={artifact} onOpen={onOpen} onRefresh={() => void sheet.refetch()} onDelete={onDelete} />
      </td>
    </tr>
  );
}

function CreateDialog({
  kind,
  onOpenChange,
  onCreated,
}: {
  kind: "chart" | "dashboard" | null;
  onOpenChange: (open: boolean) => void;
  onCreated: (artifact: StudioArtifact) => void;
}) {
  const [title, setTitle] = useState("");
  const [sqlText, setSqlText] = useState("");
  const create = useMutation({
    mutationFn: () =>
      studioApi.createArtifact(
        kind === "dashboard"
          ? {
              title: title.trim(),
              artifact_type: "dashboard",
              sql_text: "",
              viz_spec: { version: 1, title: title.trim(), zones: [] },
            }
          : { title: title.trim(), artifact_type: "chart", sql_text: sqlText.trim() },
      ),
    onSuccess: (artifact) => {
      setTitle("");
      setSqlText("");
      onOpenChange(false);
      onCreated(artifact);
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const valid = title.trim().length > 0 && (kind === "dashboard" || sqlText.trim().length > 0);
  return (
    <Dialog open={kind !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{kind === "dashboard" ? "New dashboard" : "New sheet"}</DialogTitle>
          <DialogDescription>
            {kind === "dashboard"
              ? "Start an empty dashboard, then add sheets to its layout."
              : "A sheet is built over one read-only query. Its fields become the data pane."}
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid) create.mutate();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="new-title">Name</Label>
            <Input id="new-title" value={title} maxLength={256} onChange={(event) => setTitle(event.target.value)} />
          </div>
          {kind === "chart" ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="new-sql">Query</Label>
              <Textarea
                id="new-sql"
                value={sqlText}
                onChange={(event) => setSqlText(event.target.value)}
                placeholder="SELECT * FROM sales.orders"
                className="min-h-32 font-mono text-xs"
              />
            </div>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!valid || create.isPending}>
              {create.isPending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : null}
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

