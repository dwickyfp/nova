import { useEffect, useId, useMemo, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { LayoutDashboard, Loader2, Search } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/auth-store";
import {
  datavizApi,
  type DatavizCapability,
  type DatavizLocator,
  type DatavizTarget,
} from "./api";
import {
  datavizFrameState,
  probeUnsavedChanges,
  reloadDatavizFrameIfClean,
} from "./dashboard-bridge";
import { useDatavizCapability } from "./use-dataviz-capability";

/**
 * **Add to Dataviz** for one result. Rendered only when Nova's server resolver
 * says the result is supported and the active role may author; otherwise it
 * renders nothing, so unsupported views keep their plain card.
 */
export function AddToDatavizButton({
  locator,
  className,
}: {
  locator: DatavizLocator | null;
  className?: string;
}) {
  // A result Nova has not stored yet (still streaming, read-only views) has
  // nothing to verify, so it asks nothing and shows nothing.
  return locator ? <AddToDatavizAction locator={locator} className={className} /> : null;
}

function AddToDatavizAction({
  locator,
  className,
}: {
  locator: DatavizLocator;
  className?: string;
}) {
  const capability = useDatavizCapability(locator);
  const [open, setOpen] = useState(false);
  if (!capability?.can_add || !capability.preview_ref) return null;
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className={cn("h-7 gap-1.5 px-2 text-xs", className)}
        onClick={(event) => {
          event.stopPropagation();
          setOpen(true);
        }}
      >
        <LayoutDashboard aria-hidden="true" className="size-3.5" />
        Add to Dataviz
      </Button>
      {open ? (
        <AddToDatavizDialog capability={capability} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}

function newKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function AddToDatavizDialog({
  capability,
  onClose,
  preselectDashboardId,
}: {
  capability: DatavizCapability;
  onClose: () => void;
  preselectDashboardId?: number | null;
}) {
  const navigate = useNavigate();
  const securityEpoch = useAuthStore((state) => state.securityEpoch);
  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<number | null>(preselectDashboardId ?? null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One request id per dialog: a double click or a retry is the same request.
  const [idempotencyKey] = useState(newKey);
  const listId = useId();

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const dashboards = useInfiniteQuery({
    queryKey: ["dataviz", "dashboards", securityEpoch, query],
    queryFn: ({ pageParam, signal }) => datavizApi.dashboards(query, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: (page) => (page.has_more ? page.page + 1 : undefined),
    enabled: mode === "existing",
    retry: false,
  });
  const items = useMemo(
    () => dashboards.data?.pages.flatMap((page) => page.dashboards) ?? [],
    [dashboards.data],
  );
  const concepts = (capability.concepts ?? []).filter((item) => item.label || item.qualified_name);

  const submit = async () => {
    if (!capability.preview_ref || busy) return;
    const target: DatavizTarget | null =
      mode === "new"
        ? title.trim()
          ? { mode: "new", title: title.trim() }
          : null
        : selected
          ? { mode: "existing", dashboard_id: selected }
          : null;
    if (!target) {
      setError(mode === "new" ? "Name the new dashboard." : "Choose a dashboard.");
      return;
    }
    const frame = datavizFrameState();
    const editorState = probeUnsavedChanges();
    setBusy(true);
    setError(null);
    try {
      const result = await datavizApi.publish({
        preview_ref: capability.preview_ref,
        idempotency_key: idempotencyKey,
        target,
        editor_state: editorState,
        open_dashboard_id: frame.dashboardId,
      });
      if (frame.dashboardId === result.dashboard_id) reloadDatavizFrameIfClean();
      const path = result.bi_view_path;
      toast.success(
        result.reused_chart ? "Chart placed on the dashboard" : "Chart added to Dataviz",
        path
          ? {
              action: {
                label: "Open in Dataviz",
                onClick: () =>
                  void navigate({
                    to: "/studio",
                    search: (prev) => ({ ...prev, tab: "dataviz", bi: path }),
                  }),
              },
            }
          : undefined,
      );
      onClose();
    } catch (failure) {
      setError(
        failure instanceof ApiError
          ? failure.message
          : "Dataviz could not be reached. Nothing was changed; try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => (!open && !busy ? onClose() : undefined)}>
      <DialogContent className="sm:max-w-lg" onClick={(event) => event.stopPropagation()}>
        <DialogHeader>
          <DialogTitle>Add to Dataviz</DialogTitle>
          <DialogDescription>
            “{capability.title || "This result"}” becomes a live dashboard chart. Everyone who
            opens it sees only the rows their own role allows.
          </DialogDescription>
        </DialogHeader>

        {concepts.length ? (
          <p className="text-sm text-muted-foreground">
            Business meaning:{" "}
            {concepts.map((item) => item.label || item.qualified_name).join(", ")}
          </p>
        ) : null}

        <div role="radiogroup" aria-label="Destination" className="grid grid-cols-2 gap-2">
          {(["existing", "new"] as const).map((value) => (
            <Button
              key={value}
              type="button"
              role="radio"
              aria-checked={mode === value}
              variant={mode === value ? "default" : "outline"}
              onClick={() => {
                setMode(value);
                setError(null);
              }}
            >
              {value === "existing" ? "Existing dashboard" : "New dashboard"}
            </Button>
          ))}
        </div>

        {mode === "existing" ? (
          <div className="flex min-h-0 flex-col gap-2">
            <div className="relative">
              <Search
                aria-hidden="true"
                className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                aria-label="Search dashboards"
                placeholder="Search dashboards you can edit"
                className="pl-8"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <div
              id={listId}
              role="listbox"
              aria-label="Dashboards you can edit"
              className="max-h-64 overflow-y-auto rounded-md border"
            >
              {dashboards.isLoading ? (
                <p role="status" className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
                  <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                  Loading dashboards
                </p>
              ) : dashboards.isError ? (
                <div className="flex flex-col items-start gap-2 p-3 text-sm">
                  <p>Your dashboards could not load.</p>
                  <Button type="button" size="sm" variant="outline" onClick={() => void dashboards.refetch()}>
                    Retry
                  </Button>
                </div>
              ) : items.length === 0 ? (
                <p className="p-3 text-sm text-muted-foreground">
                  {query
                    ? "No dashboard you can edit matches that name."
                    : "You have no dashboard you can edit yet. Create a new one instead."}
                </p>
              ) : (
                items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="option"
                    aria-selected={selected === item.id}
                    onClick={() => setSelected(item.id)}
                    className={cn(
                      "flex w-full items-center justify-between gap-3 border-b px-3 py-2 text-left text-sm last:border-b-0",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                      selected === item.id ? "bg-accent text-accent-foreground" : "hover:bg-muted",
                    )}
                  >
                    <span className="min-w-0 truncate font-medium">{item.title}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {item.changed ?? ""}
                    </span>
                  </button>
                ))
              )}
              {dashboards.hasNextPage ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="w-full"
                  disabled={dashboards.isFetchingNextPage}
                  onClick={() => void dashboards.fetchNextPage()}
                >
                  {dashboards.isFetchingNextPage ? "Loading" : "Show more"}
                </Button>
              ) : null}
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${listId}-title`}>Dashboard name</Label>
            <Input
              id={`${listId}-title`}
              autoFocus
              maxLength={250}
              placeholder="Sales overview"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void submit();
              }}
            />
          </div>
        )}

        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="button" onClick={() => void submit()} disabled={busy}>
            {busy ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : null}
            {mode === "new" ? "Create and add" : "Add chart"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
