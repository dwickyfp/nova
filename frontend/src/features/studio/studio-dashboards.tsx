import { useEffect, useMemo, useRef, useState, type DragEvent, type PointerEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart3, Eye, Grip, LayoutDashboard, Pencil, Plus, RefreshCw, Search, Table2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Input } from "@/components/ui/input";
import { ChartBlock } from "@/features/agents/chart-block";
import {
  studioApi,
  type StudioArtifact,
  type StudioDashboardDetail,
  type StudioDashboardTile,
} from "@/features/agents/api";
import { chartSpecWithRows } from "./studio-artifacts";
import {
  canPlaceTile,
  DASHBOARD_COLUMNS,
  DASHBOARD_ROWS,
  DEFAULT_TILE_HEIGHT,
  DEFAULT_TILE_WIDTH,
  firstFreePosition,
  tilePixelRect,
} from "./dashboard-layout";

type DragItem =
  | { kind: "artifact"; artifactId: string }
  | { kind: "tile"; tileId: string };

type DropPreview = { tile: StudioDashboardTile; valid: boolean };

export function StudioDashboards() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [initialMode, setInitialMode] = useState<"view" | "edit">("view");
  const [newTitle, setNewTitle] = useState("");
  const dashboards = useQuery({
    queryKey: ["studio", "dashboards"],
    queryFn: studioApi.listDashboards,
  });
  const create = useMutation({
    mutationFn: studioApi.createDashboard,
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["studio", "dashboards"] });
      setNewTitle("");
      setInitialMode("edit");
      setSelectedId(created.dashboard_id);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  if (selectedId) {
    return (
      <DashboardDesigner
        key={selectedId}
        dashboardId={selectedId}
        initialMode={initialMode}
        onBack={() => setSelectedId(null)}
        onDeleted={() => {
          void queryClient.invalidateQueries({ queryKey: ["studio", "dashboards"] });
          setSelectedId(null);
        }}
      />
    );
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-y-auto px-6 py-7 md:px-10">
      <div className="mx-auto w-full max-w-5xl">
        <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Dashboards</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Arrange saved artifacts on a 6 × 4 canvas.
            </p>
          </div>
          <form
            className="flex w-full max-w-sm gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              const title = newTitle.trim() || "Untitled dashboard";
              create.mutate(title);
            }}
          >
            <Input
              aria-label="New dashboard name"
              placeholder="Dashboard name"
              value={newTitle}
              onChange={(event) => setNewTitle(event.target.value)}
              maxLength={256}
            />
            <Button type="submit" disabled={create.isPending}>
              <Plus aria-hidden="true" className="size-4" />
              Create
            </Button>
          </form>
        </div>
        {dashboards.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading dashboards…</p>
        ) : dashboards.isError ? (
          <div className="flex items-center gap-3 text-sm">
            <span>Dashboards could not be loaded.</span>
            <Button variant="outline" size="sm" onClick={() => void dashboards.refetch()}>
              Try again
            </Button>
          </div>
        ) : !dashboards.data?.count ? (
          <div className="rounded-xl border border-dashed px-8 py-16 text-center">
            <LayoutDashboard aria-hidden="true" className="mx-auto mb-3 size-7 text-muted-foreground" />
            <p className="font-medium">No dashboards yet</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Create one, then add artifacts from the panel on the right.
            </p>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {dashboards.data.dashboards.map((dashboard) => (
              <button
                key={dashboard.dashboard_id}
                type="button"
                className="group rounded-xl border bg-card p-5 text-left transition-colors hover:border-primary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => {
                  setInitialMode("view");
                  setSelectedId(dashboard.dashboard_id);
                }}
              >
                <LayoutDashboard aria-hidden="true" className="mb-5 size-5 text-primary" />
                <span className="block truncate font-medium">{dashboard.title}</span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  Updated {new Date(dashboard.updated_at).toLocaleDateString()}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function DashboardDesigner({
  dashboardId,
  initialMode,
  onBack,
  onDeleted,
}: {
  dashboardId: string;
  initialMode: "view" | "edit";
  onBack: () => void;
  onDeleted: () => void;
}) {
  const queryClient = useQueryClient();
  const dashboard = useQuery({
    queryKey: ["studio", "dashboard", dashboardId],
    queryFn: () => studioApi.getDashboard(dashboardId),
  });
  const artifacts = useQuery({
    queryKey: ["studio", "artifacts"],
    queryFn: studioApi.listArtifacts,
  });
  const [title, setTitle] = useState("");
  const [tiles, setTiles] = useState<StudioDashboardTile[]>([]);
  const [mode, setMode] = useState<"view" | "edit">(initialMode);
  const [pendingAction, setPendingAction] = useState<"back" | "view" | "delete" | null>(null);
  const [initialized, setInitialized] = useState(false);
  const [search, setSearch] = useState("");
  const [dragItem, setDragItem] = useState<DragItem | null>(null);
  const [preview, setPreview] = useState<DropPreview | null>(null);
  const boardRef = useRef<HTMLDivElement | null>(null);
  const [boardSize, setBoardSize] = useState({ width: 0, height: 0, gap: 8 });
  const [resizingTileId, setResizingTileId] = useState<string | null>(null);
  const resizeRef = useRef<{
    tile: StudioDashboardTile;
    startX: number;
    startY: number;
  } | null>(null);

  useEffect(() => {
    if (!dashboard.data || initialized) return;
    setTitle(dashboard.data.title);
    setTiles(dashboard.data.layout.tiles);
    setInitialized(true);
  }, [dashboard.data, initialized]);

  useEffect(() => {
    const board = boardRef.current;
    if (!board) return;
    const measure = () => {
      const next = {
        width: board.clientWidth,
        height: board.clientHeight,
        gap: parseFloat(getComputedStyle(board).columnGap) || 0,
      };
      setBoardSize((current) =>
        current.width === next.width && current.height === next.height && current.gap === next.gap
          ? current
          : next,
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(board);
    return () => observer.disconnect();
  }, [initialized, mode]);

  const save = useMutation({
    mutationFn: (current: StudioDashboardDetail) =>
      studioApi.updateDashboard(dashboardId, {
        title: title.trim() || "Untitled dashboard",
        layout: { tiles },
        expected_updated_at: current.updated_at,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["studio", "dashboard", dashboardId], updated);
      void queryClient.invalidateQueries({ queryKey: ["studio", "dashboards"] });
      setMode("view");
      toast.success("Dashboard saved");
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: () => studioApi.deleteDashboard(dashboardId),
    onSuccess: onDeleted,
    onError: (error: Error) => toast.error(error.message),
  });

  const available = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (artifacts.data?.artifacts ?? []).filter((artifact) =>
      artifact.title.toLowerCase().includes(needle),
    );
  }, [artifacts.data?.artifacts, search]);
  const artifactById = useMemo(
    () => new Map((artifacts.data?.artifacts ?? []).map((item) => [item.artifact_id, item])),
    [artifacts.data?.artifacts],
  );
  const dirty = Boolean(
    initialized && dashboard.data &&
      (title !== dashboard.data.title ||
        JSON.stringify(tiles) !== JSON.stringify(dashboard.data.layout.tiles)),
  );

  const addArtifact = (artifactId: string, position?: { x: number; y: number }) => {
    const spot = position ?? firstFreePosition(tiles);
    if (!spot) {
      toast.error("No 3 × 2 space is available. Resize or remove a tile first.");
      return;
    }
    const tile: StudioDashboardTile = {
      tile_id: crypto.randomUUID(),
      artifact_id: artifactId,
      x: spot.x,
      y: spot.y,
      w: DEFAULT_TILE_WIDTH,
      h: DEFAULT_TILE_HEIGHT,
    };
    if (!canPlaceTile(tiles, tile)) {
      toast.error("That space is occupied.");
      return;
    }
    setTiles((current) => [...current, tile]);
  };

  const updateTile = (next: StudioDashboardTile) => {
    setTiles((current) => {
      if (!canPlaceTile(current, next)) return current;
      const previous = current.find((tile) => tile.tile_id === next.tile_id);
      if (previous && previous.x === next.x && previous.y === next.y && previous.w === next.w && previous.h === next.h) {
        return current;
      }
      return current.map((tile) => (tile.tile_id === next.tile_id ? next : tile));
    });
  };

  const pointToCell = (clientX: number, clientY: number) => {
    const board = boardRef.current;
    if (!board) return null;
    const rect = board.getBoundingClientRect();
    const style = getComputedStyle(board);
    const gap = parseFloat(style.columnGap) || 0;
    const rowGap = parseFloat(style.rowGap) || 0;
    const left = parseFloat(style.paddingLeft) || 0;
    const top = parseFloat(style.paddingTop) || 0;
    const width = (rect.width - left - (parseFloat(style.paddingRight) || 0) - gap * (DASHBOARD_COLUMNS - 1)) / DASHBOARD_COLUMNS;
    const height = (rect.height - top - (parseFloat(style.paddingBottom) || 0) - rowGap * (DASHBOARD_ROWS - 1)) / DASHBOARD_ROWS;
    const x = Math.floor((clientX - rect.left - left) / (width + gap));
    const y = Math.floor((clientY - rect.top - top) / (height + rowGap));
    if (x < 0 || x >= DASHBOARD_COLUMNS || y < 0 || y >= DASHBOARD_ROWS) return null;
    return { x, y };
  };

  const candidateAt = (item: DragItem, x: number, y: number): StudioDashboardTile | null => {
    if (item.kind === "artifact") {
      return {
        tile_id: "__preview__",
        artifact_id: item.artifactId,
        x: Math.min(x, DASHBOARD_COLUMNS - DEFAULT_TILE_WIDTH),
        y: Math.min(y, DASHBOARD_ROWS - DEFAULT_TILE_HEIGHT),
        w: DEFAULT_TILE_WIDTH,
        h: DEFAULT_TILE_HEIGHT,
      };
    }
    const original = tiles.find((tile) => tile.tile_id === item.tileId);
    if (!original) return null;
    return {
      ...original,
      x: Math.min(x, DASHBOARD_COLUMNS - original.w),
      y: Math.min(y, DASHBOARD_ROWS - original.h),
    };
  };

  const onBoardDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!dragItem) return;
    event.preventDefault();
    const cell = pointToCell(event.clientX, event.clientY);
    const candidate = cell && candidateAt(dragItem, cell.x, cell.y);
    setPreview(candidate ? { tile: candidate, valid: canPlaceTile(tiles, candidate) } : null);
  };

  const onBoardDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const cell = pointToCell(event.clientX, event.clientY);
    const candidate = dragItem && cell && candidateAt(dragItem, cell.x, cell.y);
    if (candidate && canPlaceTile(tiles, candidate)) {
      if (dragItem?.kind === "artifact" && artifactById.has(dragItem.artifactId)) {
        addArtifact(dragItem.artifactId, { x: candidate.x, y: candidate.y });
      } else if (dragItem?.kind === "tile") {
        updateTile(candidate);
      }
    }
    setDragItem(null);
    setPreview(null);
  };

  const onResizeStart = (tile: StudioDashboardTile, event: PointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    resizeRef.current = { tile, startX: event.clientX, startY: event.clientY };
    setResizingTileId(tile.tile_id);
  };

  const onResizeMove = (event: PointerEvent<HTMLButtonElement>) => {
    const start = resizeRef.current;
    const board = boardRef.current;
    if (!start || !board) return;
    const rect = board.getBoundingClientRect();
    const style = getComputedStyle(board);
    const columnStep = (rect.width - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0) + (parseFloat(style.columnGap) || 0)) / DASHBOARD_COLUMNS;
    const rowStep = (rect.height - (parseFloat(style.paddingTop) || 0) - (parseFloat(style.paddingBottom) || 0) + (parseFloat(style.rowGap) || 0)) / DASHBOARD_ROWS;
    const w = Math.max(1, Math.min(DASHBOARD_COLUMNS - start.tile.x,
      start.tile.w + Math.round((event.clientX - start.startX) / columnStep)));
    const h = Math.max(1, Math.min(DASHBOARD_ROWS - start.tile.y,
      start.tile.h + Math.round((event.clientY - start.startY) / rowStep)));
    updateTile({ ...start.tile, w, h });
  };

  if (dashboard.isLoading || (dashboard.data && !initialized)) {
    return <p className="p-8 text-sm text-muted-foreground">Loading dashboard…</p>;
  }
  if (dashboard.isError || !dashboard.data) {
    return (
      <div className="p-8 text-sm">
        <p>Dashboard could not be loaded.</p>
        <Button variant="outline" className="mt-3" onClick={onBack}>Back to dashboards</Button>
      </div>
    );
  }

  const confirmAction = () => {
    const action = pendingAction;
    setPendingAction(null);
    if (action === "back") onBack();
    if (action === "view") {
      setTitle(dashboard.data.title);
      setTiles(dashboard.data.layout.tiles);
      setMode("view");
    }
    if (action === "delete") remove.mutate();
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-3 border-b px-5 py-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            if (dirty) setPendingAction("back");
            else onBack();
          }}
        >
          ← Dashboards
        </Button>
        {mode === "edit" ? (
          <Input
            aria-label="Dashboard name"
            className="min-w-48 max-w-md flex-1 text-base font-medium"
            maxLength={256}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        ) : (
          <h1 className="min-w-0 flex-1 truncate text-base font-semibold">{title}</h1>
        )}
        <Button
          type="button"
          size={mode === "edit" && dirty ? "sm" : "icon"}
          className={mode === "edit" && dirty ? "ml-auto" : "ml-auto size-8"}
          variant={mode === "edit" && !dirty ? "secondary" : "outline"}
          aria-label={mode === "view" ? "Edit dashboard" : dirty ? undefined : "View dashboard"}
          onClick={() => {
            if (mode === "view") {
              setMode("edit");
              return;
            }
            if (dirty) {
              setPendingAction("view");
              return;
            }
            setTitle(dashboard.data.title);
            setTiles(dashboard.data.layout.tiles);
            setMode("view");
          }}
        >
          {mode === "view" ? (
            <Pencil aria-hidden="true" className="size-4" />
          ) : dirty ? (
            "Discard Changes"
          ) : (
            <Eye aria-hidden="true" className="size-4" />
          )}
        </Button>
        {mode === "edit" ? (
          <>
            <Button
              variant="outline"
              size="sm"
              aria-label="Delete dashboard"
              disabled={remove.isPending}
              onClick={() => setPendingAction("delete")}
            >
              <Trash2 aria-hidden="true" className="size-4" />
            </Button>
            <Button size="sm" disabled={!dirty || !title.trim() || save.isPending} onClick={() => save.mutate(dashboard.data)}>
              {save.isPending ? "Saving…" : "Save dashboard"}
            </Button>
          </>
        ) : null}
      </header>
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="min-h-0 min-w-0 flex-1 overflow-auto p-3">
          <div className="h-full min-h-0 w-full overflow-x-auto">
              <div
                ref={boardRef}
                data-testid="dashboard-grid"
                data-mode={mode}
                className={mode === "edit"
                  ? "relative grid h-full min-w-[780px] w-full gap-2 bg-muted/20"
                  : "relative h-full min-w-[780px] w-full gap-2 bg-background"}
                style={{
                  gridTemplateColumns: `repeat(${DASHBOARD_COLUMNS}, minmax(0, 1fr))`,
                  gridTemplateRows: `repeat(${DASHBOARD_ROWS}, minmax(0, 1fr))`,
                }}
                onDragOver={mode === "edit" ? onBoardDragOver : undefined}
                onDrop={mode === "edit" ? onBoardDrop : undefined}
              >
                {mode === "edit" ? Array.from({ length: DASHBOARD_COLUMNS * DASHBOARD_ROWS }, (_, index) => (
                  <div
                    key={index}
                    aria-hidden="true"
                    className="rounded-md border border-dashed border-border/70 bg-background/30"
                    style={{
                      gridColumn: (index % DASHBOARD_COLUMNS) + 1,
                      gridRow: Math.floor(index / DASHBOARD_COLUMNS) + 1,
                    }}
                  />
                )) : null}
                {tiles.map((tile) => (
                  <DashboardTileCard
                    key={tile.tile_id}
                    tile={tile}
                    mode={mode}
                    resizing={resizingTileId === tile.tile_id}
                    rect={tilePixelRect(tile, boardSize.width, boardSize.height, boardSize.gap)}
                    artifact={artifactById.get(tile.artifact_id)}
                    artifactsLoading={artifacts.isLoading}
                    onRemove={() => setTiles((current) => current.filter((item) => item.tile_id !== tile.tile_id))}
                    onMove={(x, y) => updateTile({ ...tile, x, y })}
                    onResize={(w, h) => updateTile({ ...tile, w, h })}
                    onDragStart={(event) => {
                      event.dataTransfer.effectAllowed = "move";
                      event.dataTransfer.setData("text/plain", tile.tile_id);
                      setDragItem({ kind: "tile", tileId: tile.tile_id });
                    }}
                    onDragEnd={() => { setDragItem(null); setPreview(null); }}
                    onResizeStart={(event) => onResizeStart(tile, event)}
                    onResizeMove={onResizeMove}
                    onResizeEnd={() => {
                      resizeRef.current = null;
                      setResizingTileId(null);
                    }}
                  />
                ))}
                {mode === "edit" && preview ? (
                  <div
                    aria-hidden="true"
                    className={`pointer-events-none absolute z-20 rounded-lg border-2 transition-[left,top,width,height] duration-200 ease-out ${preview.valid ? "border-primary bg-primary/15" : "border-destructive bg-destructive/15"}`}
                    style={tilePixelRect(preview.tile, boardSize.width, boardSize.height, boardSize.gap)}
                  />
                ) : null}
              </div>
          </div>
        </div>
        {mode === "edit" ? (
        <aside className="flex min-h-0 w-full shrink-0 basis-1/3 flex-col border-t bg-background lg:w-72 lg:basis-auto lg:border-l lg:border-t-0">
          <div className="shrink-0 border-b p-4">
            <h2 className="font-medium">Artifacts</h2>
            <p className="mt-1 text-xs text-muted-foreground">Drag a saved chart or table to the canvas.</p>
            <div className="relative mt-3">
              <Search aria-hidden="true" className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="pl-8"
                aria-label="Search dashboard artifacts"
                placeholder="Search artifacts"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
          </div>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
            {artifacts.isLoading ? (
              <p className="p-2 text-sm text-muted-foreground">Loading artifacts…</p>
            ) : artifacts.isError ? (
              <p className="p-2 text-sm text-destructive">Artifacts could not be loaded.</p>
            ) : available.length === 0 ? (
              <p className="p-2 text-sm text-muted-foreground">
                {search ? "No matching artifacts." : "Save a chart or table in Studio first."}
              </p>
            ) : available.map((artifact) => (
              <div
                key={artifact.artifact_id}
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.effectAllowed = "copy";
                  event.dataTransfer.setData("text/plain", artifact.artifact_id);
                  setDragItem({ kind: "artifact", artifactId: artifact.artifact_id });
                }}
                onDragEnd={() => { setDragItem(null); setPreview(null); }}
                className="flex items-center gap-2 rounded-lg border bg-card p-2.5"
              >
                {artifact.artifact_type === "chart" ? (
                  <BarChart3 aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
                ) : (
                  <Table2 aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
                )}
                <span className="min-w-0 flex-1 truncate text-sm" title={artifact.title}>{artifact.title}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  aria-label={`Add ${artifact.title} to dashboard`}
                  onClick={() => addArtifact(artifact.artifact_id)}
                >
                  <Plus aria-hidden="true" className="size-4" />
                </Button>
              </div>
            ))}
          </div>
        </aside>
        ) : null}
      </div>
      <ConfirmDialog
        open={pendingAction !== null}
        onOpenChange={(open) => { if (!open) setPendingAction(null); }}
        title={pendingAction === "delete" ? "Delete dashboard?" : "Discard unsaved changes?"}
        desc={pendingAction === "delete"
          ? `“${dashboard.data.title}” will be deleted.`
          : "Your changes to this dashboard will be lost."}
        confirmText={pendingAction === "delete" ? "Delete dashboard" : "Discard changes"}
        destructive
        handleConfirm={confirmAction}
      />
    </div>
  );
}

function DashboardTileCard({
  tile,
  mode,
  resizing,
  rect,
  artifact,
  artifactsLoading,
  onRemove,
  onMove,
  onResize,
  onDragStart,
  onDragEnd,
  onResizeStart,
  onResizeMove,
  onResizeEnd,
}: {
  tile: StudioDashboardTile;
  mode: "view" | "edit";
  resizing: boolean;
  rect: { left: number; top: number; width: number; height: number };
  artifact: StudioArtifact | undefined;
  artifactsLoading: boolean;
  onRemove: () => void;
  onMove: (x: number, y: number) => void;
  onResize: (w: number, h: number) => void;
  onDragStart: (event: DragEvent<HTMLDivElement>) => void;
  onDragEnd: () => void;
  onResizeStart: (event: PointerEvent<HTMLButtonElement>) => void;
  onResizeMove: (event: PointerEvent<HTMLButtonElement>) => void;
  onResizeEnd: () => void;
}) {
  const data = useQuery({
    queryKey: ["studio", "artifact-data", tile.artifact_id],
    queryFn: () => studioApi.refreshArtifact(tile.artifact_id),
    enabled: Boolean(artifact),
  });
  return (
    <article
      data-resizing={resizing ? "true" : "false"}
      className={`absolute z-10 flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border bg-card shadow-sm transition-[left,top,width,height,opacity,border-color,box-shadow] duration-200 ease-out motion-reduce:transition-none ${resizing ? "border-destructive bg-destructive/10 opacity-70 ring-2 ring-destructive/60" : ""}`}
      style={rect}
    >
      <div
        draggable={mode === "edit"}
        tabIndex={mode === "edit" ? 0 : undefined}
        role={mode === "edit" ? "button" : undefined}
        aria-label={mode === "edit" ? `Move ${artifact?.title ?? "unavailable artifact"}` : undefined}
        className={`flex shrink-0 items-center gap-2 border-b px-3 py-2 text-sm ${mode === "edit" ? "cursor-grab focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring active:cursor-grabbing" : ""}`}
        onDragStart={mode === "edit" ? onDragStart : undefined}
        onDragEnd={mode === "edit" ? onDragEnd : undefined}
        onKeyDown={(event) => {
          if (mode !== "edit") return;
          const delta = {
            ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
          }[event.key];
          if (delta) {
            event.preventDefault();
            onMove(tile.x + delta[0], tile.y + delta[1]);
          }
        }}
      >
        {mode === "edit" ? <Grip aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" /> : null}
        <span className="min-w-0 flex-1 truncate font-medium">{artifact?.title ?? (artifactsLoading ? "Loading artifact…" : "Artifact unavailable")}</span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label={`Refresh ${artifact?.title ?? "artifact"}`}
          disabled={!artifact || data.isFetching}
          onClick={(event) => { event.stopPropagation(); void data.refetch(); }}
        >
          <RefreshCw aria-hidden="true" className="size-3.5" />
        </Button>
        {mode === "edit" ? <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label={`Remove ${artifact?.title ?? "artifact"} from dashboard`}
          onClick={(event) => { event.stopPropagation(); onRemove(); }}
        >
          <Trash2 aria-hidden="true" className="size-3.5" />
        </Button> : null}
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {artifactsLoading ? (
          <p className="text-xs text-muted-foreground">Loading artifact…</p>
        ) : !artifact ? (
          <p className="text-xs text-muted-foreground">This artifact was deleted. Remove the tile to save the dashboard.</p>
        ) : data.isLoading ? (
          <p className="text-xs text-muted-foreground">Loading artifact…</p>
        ) : data.isError || !data.data ? (
          <p className="text-xs text-destructive">Artifact data could not be loaded.</p>
        ) : data.data.artifact.artifact_type === "chart" && data.data.artifact.chart_spec ? (
          <ChartBlock spec={chartSpecWithRows(data.data)} fillHeight />
        ) : (
          <table className="w-full border-collapse text-xs">
            <thead><tr>{data.data.columns.map((column) => <th key={column} className="border-b p-1.5 text-left font-medium">{column}</th>)}</tr></thead>
            <tbody>{data.data.rows.slice(0, 20).map((row, index) => (
              <tr key={index} className="border-b last:border-0">
                {row.map((value, cellIndex) => <td key={cellIndex} className="whitespace-nowrap p-1.5">{value == null ? "—" : String(value)}</td>)}
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
      {mode === "edit" ? <button
        type="button"
        aria-label={`Resize ${artifact?.title ?? "artifact"}. Use arrow keys to change width and height.`}
        className="absolute bottom-0 right-0 z-10 flex size-6 cursor-nwse-resize items-center justify-center rounded-tl bg-card text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onPointerDown={onResizeStart}
        onPointerMove={onResizeMove}
        onPointerUp={onResizeEnd}
        onPointerCancel={onResizeEnd}
        onKeyDown={(event) => {
          if (event.key === "ArrowRight") onResize(tile.w + 1, tile.h);
          else if (event.key === "ArrowLeft") onResize(tile.w - 1, tile.h);
          else if (event.key === "ArrowDown") onResize(tile.w, tile.h + 1);
          else if (event.key === "ArrowUp") onResize(tile.w, tile.h - 1);
          else return;
          event.preventDefault();
        }}
      >
        <Grip aria-hidden="true" className="size-3.5 rotate-90" />
      </button> : null}
    </article>
  );
}
