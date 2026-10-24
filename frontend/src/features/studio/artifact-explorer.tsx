import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  BarChart3,
  LayoutDashboard,
  LayoutGrid,
  List,
  Loader2,
  MoreHorizontal,
  PackageOpen,
  Plus,
  Search,
  Star,
} from "lucide-react";
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
import type { DashboardSpec, PresModel } from "@/features/viz/types";
import { VizView } from "@/features/viz/viz-view";
import { cn } from "@/lib/utils";

/**
 * ArtifactExplorer is the Studio content browser: dashboards and sheets as
 * thumbnails or a sortable list, filtered by kind, searchable, with
 * favorites, and actions to open, edit, refresh, delete, or create a new
 * sheet or dashboard. Thumbnails are live: sheets compute their view and
 * dashboards draw their zones with each sheet's marks.
 */

type Kind = "all" | "dashboard" | "chart" | "favorites";
type SortKey = "updated" | "created" | "title" | "type";
type Layout = "grid" | "list";

const FAVORITES_KEY = "nova.studio.favorite-artifacts";
const LAYOUT_KEY = "nova.studio.explorer-layout";

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
    // Favorites and layout are conveniences; nothing depends on them.
  }
}

function formatModified(value: string): string {
  const date = new Date(value.includes("T") || value.endsWith("Z") ? value : `${value.replace(" ", "T")}Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
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
  const [layout, setLayoutState] = useState<Layout>(() => readStored<Layout>(LAYOUT_KEY, "grid"));
  const [favorites, setFavorites] = useState<string[]>(() => readStored<string[]>(FAVORITES_KEY, []));
  const [creating, setCreating] = useState<"chart" | "dashboard" | null>(null);
  const [deleting, setDeleting] = useState<StudioArtifact | null>(null);

  const artifactsQuery = useQuery({
    queryKey: ["studio", "artifacts"],
    queryFn: () => studioApi.listArtifacts(),
  });

  const setLayout = (next: Layout) => {
    setLayoutState(next);
    writeStored(LAYOUT_KEY, next);
  };
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

  const all = artifactsQuery.data?.artifacts ?? [];
  const counts = {
    all: all.length,
    dashboard: all.filter((item) => item.artifact_type === "dashboard").length,
    chart: all.filter((item) => item.artifact_type === "chart").length,
    favorites: all.filter((item) => favorites.includes(item.artifact_id)).length,
  };

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const rows = all.filter((item) => {
      if (kind === "favorites" && !favorites.includes(item.artifact_id)) return false;
      if ((kind === "dashboard" || kind === "chart") && item.artifact_type !== kind) return false;
      return !needle || item.title.toLowerCase().includes(needle);
    });
    const value = (item: StudioArtifact): string =>
      sort.key === "title"
        ? item.title.toLowerCase()
        : sort.key === "type"
          ? item.artifact_type
          : sort.key === "created"
            ? item.created_at
            : item.updated_at;
    rows.sort((a, b) => {
      const order = value(a) < value(b) ? -1 : value(a) > value(b) ? 1 : 0;
      return sort.direction === "asc" ? order : -order;
    });
    return rows;
  }, [all, favorites, kind, search, sort]);

  const tabs: { value: Kind; label: string }[] = [
    { value: "all", label: "All" },
    { value: "dashboard", label: "Dashboards" },
    { value: "chart", label: "Sheets" },
    { value: "favorites", label: "Favorites" },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b px-5 pt-3 sm:px-7">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="text-lg font-semibold">Explore</h1>
            <p className="text-xs text-muted-foreground">
              Dashboards and sheets recompute from current data, under your access, whenever they open.
            </p>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" size="sm" className="gap-1.5">
                <Plus aria-hidden="true" className="size-4" /> New
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
        <nav className="-mb-px mt-3 flex gap-1 overflow-x-auto" aria-label="Content type">
          {tabs.map((tab) => (
            <button
              key={tab.value}
              type="button"
              onClick={() => setKind(tab.value)}
              aria-current={kind === tab.value ? "page" : undefined}
              className={cn(
                "flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-ring",
                kind === tab.value
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {tab.label}
              <span className="rounded-full bg-muted px-1.5 text-xs text-muted-foreground">
                {counts[tab.value]}
              </span>
            </button>
          ))}
        </nav>
      </header>

      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-5 py-2 sm:px-7" role="toolbar" aria-label="Explore tools">
        <div className="relative w-full sm:w-64">
          <Search aria-hidden="true" className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search content"
            aria-label="Search content"
            className="h-8 pl-8"
          />
        </div>
        <span className="text-xs text-muted-foreground" role="status">
          {visible.length} {visible.length === 1 ? "item" : "items"}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Select value={sort.key} onValueChange={(value) => setSort((current) => ({ ...current, key: value as SortKey }))}>
            <SelectTrigger aria-label="Sort by" className="h-8 w-40 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="updated">Last modified</SelectItem>
              <SelectItem value="created">Date created</SelectItem>
              <SelectItem value="title">Name</SelectItem>
              <SelectItem value="type">Type</SelectItem>
            </SelectContent>
          </Select>
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="size-8"
            aria-label={sort.direction === "asc" ? "Sort descending" : "Sort ascending"}
            onClick={() => setSort((current) => ({ ...current, direction: current.direction === "asc" ? "desc" : "asc" }))}
          >
            {sort.direction === "asc" ? (
              <ArrowUp aria-hidden="true" className="size-4" />
            ) : (
              <ArrowDown aria-hidden="true" className="size-4" />
            )}
          </Button>
          <div className="flex rounded-md border p-0.5" role="group" aria-label="Layout">
            {(["grid", "list"] as const).map((option) => {
              const Icon = option === "grid" ? LayoutGrid : List;
              return (
                <button
                  key={option}
                  type="button"
                  aria-pressed={layout === option}
                  aria-label={option === "grid" ? "Grid view" : "List view"}
                  onClick={() => setLayout(option)}
                  className={cn(
                    "rounded p-1 focus-visible:outline-2 focus-visible:outline-ring",
                    layout === option ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Icon aria-hidden="true" className="size-4" />
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-7">
        {artifactsQuery.isLoading ? (
          <p className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 aria-hidden="true" className="size-4 animate-spin" /> Loading content
          </p>
        ) : artifactsQuery.isError ? (
          <EmptyState
            variant="error"
            icon={AlertCircle}
            title="Content could not be loaded"
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
                ? "Mark dashboards and sheets with the star to keep them here."
                : `No ${kind === "all" ? "content" : tabs.find((tab) => tab.value === kind)?.label.toLowerCase()} matches “${search.trim()}”.`
            }
          />
        ) : layout === "grid" ? (
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-4" aria-label="Content">
            {visible.map((artifact) => (
              <li key={artifact.artifact_id}>
                <ContentCard
                  artifact={artifact}
                  favorite={favorites.includes(artifact.artifact_id)}
                  onToggleFavorite={() => toggleFavorite(artifact.artifact_id)}
                  onOpen={(edit) => onOpen(artifact.artifact_id, { edit })}
                  onDelete={() => setDeleting(artifact)}
                />
              </li>
            ))}
          </ul>
        ) : (
          <ContentTable
            rows={visible}
            favorites={favorites}
            sort={sort}
            onSort={(key) =>
              setSort((current) => ({
                key,
                direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
              }))
            }
            onToggleFavorite={toggleFavorite}
            onOpen={(id, edit) => onOpen(id, { edit })}
            onDelete={setDeleting}
          />
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
        title={`Delete ${deleting?.title ?? "this item"}?`}
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

function TypeIcon({ type, className }: { type: StudioArtifact["artifact_type"]; className?: string }) {
  const Icon = type === "dashboard" ? LayoutDashboard : BarChart3;
  return <Icon aria-hidden="true" className={cn("size-4 shrink-0 text-muted-foreground", className)} />;
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
        <DropdownMenuItem onSelect={() => onOpen(true)}>
          {artifact.artifact_type === "dashboard" ? "Edit layout" : "Edit sheet"}
        </DropdownMenuItem>
        {onRefresh ? <DropdownMenuItem onSelect={onRefresh}>Refresh thumbnail</DropdownMenuItem> : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={onDelete}>
          Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ContentCard({
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
  const isDashboard = artifact.artifact_type === "dashboard";
  const sheet = useQuery({
    queryKey: ["studio", "artifact-data", artifact.artifact_id],
    queryFn: () => studioApi.refreshArtifact(artifact.artifact_id),
    enabled: !isDashboard,
    staleTime: 60_000,
  });
  return (
    <article
      className="group flex h-full min-w-0 flex-col overflow-hidden rounded-xl border bg-card shadow-sm transition-[border-color,box-shadow] focus-within:ring-2 focus-within:ring-ring hover:border-foreground/20 hover:shadow-md"
    >
      <button
        type="button"
        onClick={() => onOpen(false)}
        aria-label={`Open ${artifact.title}`}
        className="relative block aspect-[16/10] w-full overflow-hidden border-b bg-surface-1 text-left focus-visible:outline-none"
      >
        {isDashboard ? (
          <DashboardThumbnail artifactId={artifact.artifact_id} />
        ) : sheet.isLoading ? (
          <ThumbnailState>
            <Loader2 aria-hidden="true" className="size-4 animate-spin" />
          </ThumbnailState>
        ) : sheet.isError ? (
          <ThumbnailState>
            <AlertCircle aria-hidden="true" className="size-4" /> Data unavailable
          </ThumbnailState>
        ) : sheet.data?.presmodel ? (
          <div className="pointer-events-none h-full p-3">
            <VizView presmodel={sheet.data.presmodel as PresModel} thumbnail />
          </div>
        ) : (
          <ThumbnailState>No view yet</ThumbnailState>
        )}
      </button>
      <div className="flex items-start gap-2 px-3 py-2.5">
        <TypeIcon type={artifact.artifact_type} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-medium" title={artifact.title}>
            <button
              type="button"
              onClick={() => onOpen(false)}
              className="max-w-full truncate text-left hover:underline focus-visible:outline-none"
            >
              {artifact.title}
            </button>
          </h2>
          <p className="truncate text-xs text-muted-foreground">
            {isDashboard ? "Dashboard" : "Sheet"} · {formatModified(artifact.updated_at)}
          </p>
        </div>
        <FavoriteButton favorite={favorite} title={artifact.title} onToggle={onToggleFavorite} />
        <ItemMenu
          artifact={artifact}
          onOpen={onOpen}
          onRefresh={isDashboard ? undefined : () => void sheet.refetch()}
          onDelete={onDelete}
        />
      </div>
      {sheet.isFetching && !sheet.isLoading ? (
        <span className="sr-only" role="status">
          Refreshing {artifact.title}
        </span>
      ) : null}
    </article>
  );
}

function ThumbnailState({ children }: { children: React.ReactNode }) {
  return (
    <span className="flex h-full items-center justify-center gap-1.5 text-xs text-muted-foreground">{children}</span>
  );
}

/** A dashboard thumbnail: its zones, each drawing the sheet's marks. */
function DashboardThumbnail({ artifactId }: { artifactId: string }) {
  const detail = useQuery({
    queryKey: ["studio", "artifact", artifactId],
    queryFn: () => studioApi.getArtifact(artifactId),
    staleTime: 60_000,
  });
  const spec = detail.data?.viz_spec as unknown as DashboardSpec | null;
  const compute = useQuery({
    queryKey: ["studio", "dashboard-thumbnail", artifactId, detail.data?.updated_at],
    queryFn: () => studioApi.computeVizDashboard({ spec: spec as unknown as Record<string, unknown> }),
    enabled: Boolean(spec?.zones?.length),
    staleTime: 60_000,
  });
  if (detail.isLoading) {
    return (
      <ThumbnailState>
        <Loader2 aria-hidden="true" className="size-4 animate-spin" />
      </ThumbnailState>
    );
  }
  const zones = spec?.zones ?? [];
  if (!zones.length) return <ThumbnailState>Empty dashboard</ThumbnailState>;
  const rows = Math.max(1, ...zones.map((zone) => zone.rect.y + zone.rect.h));
  const results = new Map((compute.data?.zones ?? []).map((zone) => [zone.zone_id, zone]));
  return (
    <div
      className="pointer-events-none grid h-full gap-1 p-1.5"
      style={{
        gridTemplateColumns: "repeat(24, minmax(0, 1fr))",
        gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))`,
      }}
      aria-hidden="true"
    >
      {zones.map((zone) => {
        const result = results.get(zone.zone_id);
        return (
          <div
            key={zone.zone_id}
            className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded border bg-card p-1"
            style={{
              gridColumn: `${zone.rect.x + 1} / span ${zone.rect.w}`,
              gridRow: `${zone.rect.y + 1} / span ${zone.rect.h}`,
            }}
          >
            {zone.kind === "sheet" ? (
              <>
                <span className="truncate text-xs leading-tight text-muted-foreground">{result?.title ?? ""}</span>
                <div className="min-h-0 flex-1">
                  {result?.presmodel ? (
                    <VizView presmodel={result.presmodel} thumbnail />
                  ) : result?.error ? (
                    <span className="flex h-full items-center justify-center gap-1 text-center text-xs text-muted-foreground">
                      <AlertCircle aria-hidden="true" className="size-3 shrink-0" />
                      <span className="line-clamp-2">{result.error}</span>
                    </span>
                  ) : compute.isLoading ? (
                    <span className="block h-full animate-pulse rounded bg-muted" />
                  ) : null}
                </div>
              </>
            ) : zone.kind === "text" ? (
              <span className="truncate text-xs font-medium">{zone.text?.content}</span>
            ) : zone.kind === "filter" ? (
              <span className="truncate text-xs text-muted-foreground">Filter · {zone.filter?.field}</span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function ContentTable({
  rows,
  favorites,
  sort,
  onSort,
  onToggleFavorite,
  onOpen,
  onDelete,
}: {
  rows: StudioArtifact[];
  favorites: string[];
  sort: { key: SortKey; direction: "asc" | "desc" };
  onSort: (key: SortKey) => void;
  onToggleFavorite: (id: string) => void;
  onOpen: (id: string, edit: boolean) => void;
  onDelete: (artifact: StudioArtifact) => void;
}) {
  const header = (key: SortKey, label: string) => (
    <th scope="col" aria-sort={sort.key === key ? (sort.direction === "asc" ? "ascending" : "descending") : "none"} className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">
      <button type="button" onClick={() => onSort(key)} className="inline-flex items-center gap-1 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
        {label}
        {sort.key === key ? (
          sort.direction === "asc" ? <ArrowUp aria-hidden="true" className="size-3" /> : <ArrowDown aria-hidden="true" className="size-3" />
        ) : null}
      </button>
    </th>
  );
  return (
    <div className="overflow-x-auto rounded-xl border bg-card">
      <table className="w-full min-w-[36rem] text-sm">
        <thead className="border-b">
          <tr>
            <th scope="col" className="w-10 px-3 py-2">
              <span className="sr-only">Favorite</span>
            </th>
            {header("title", "Name")}
            {header("type", "Type")}
            {header("created", "Created")}
            {header("updated", "Modified")}
            <th scope="col" className="w-10 px-3 py-2">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((artifact) => (
            <tr key={artifact.artifact_id} className="border-b last:border-b-0 hover:bg-surface-1">
              <td className="px-3 py-1.5">
                <FavoriteButton
                  favorite={favorites.includes(artifact.artifact_id)}
                  title={artifact.title}
                  onToggle={() => onToggleFavorite(artifact.artifact_id)}
                />
              </td>
              <td className="max-w-0 px-3 py-1.5">
                <button
                  type="button"
                  onClick={() => onOpen(artifact.artifact_id, false)}
                  className="flex max-w-full items-center gap-2 text-left font-medium hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                >
                  <TypeIcon type={artifact.artifact_type} />
                  <span className="truncate">{artifact.title}</span>
                </button>
              </td>
              <td className="px-3 py-1.5 text-muted-foreground">
                {artifact.artifact_type === "dashboard" ? "Dashboard" : "Sheet"}
              </td>
              <td className="px-3 py-1.5 whitespace-nowrap text-muted-foreground">{formatModified(artifact.created_at)}</td>
              <td className="px-3 py-1.5 whitespace-nowrap text-muted-foreground">{formatModified(artifact.updated_at)}</td>
              <td className="px-3 py-1.5">
                <ItemMenu artifact={artifact} onOpen={(edit) => onOpen(artifact.artifact_id, edit)} onDelete={() => onDelete(artifact)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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

