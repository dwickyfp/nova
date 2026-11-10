import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Package, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { notebookApi } from "./api";
import type { NotebookEvent } from "./stream";
import type { VariableInfo } from "./types";

export type SidePanelTab = "outline" | "variables" | "packages";

export type OutlineItem = { id: string; label: string; titled: boolean; active: boolean };

function OutlineTab({ items, onJump }: { items: OutlineItem[]; onJump: (cellId: string) => void }) {
  if (!items.length) {
    return <p className="px-4 py-3 text-sm text-muted-foreground">No cells yet</p>;
  }
  return (
    <ol className="flex flex-col gap-0.5 p-2">
      {items.map((item, index) => (
        <li key={item.id}>
          <button
            type="button"
            onClick={() => onJump(item.id)}
            aria-current={item.active ? "true" : undefined}
            className={cn(
              "flex h-8 w-full items-center gap-3 rounded-md px-2 text-left text-sm transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring",
              item.active ? "bg-muted text-foreground" : "text-muted-foreground",
            )}
          >
            <span className="w-5 shrink-0 text-right font-mono text-xs tabular-nums">{index + 1}</span>
            <span className={cn("truncate", item.titled && "text-foreground")}>{item.label}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function VariablesTab({
  variables,
  ready,
}: {
  variables: VariableInfo[] | null;
  ready: boolean;
}) {
  if (!ready) {
    return (
      <EmptyState
        title="No runtime"
        description="Start the runtime and run a cell to see the variables it defines"
      />
    );
  }
  if (!variables || variables.length === 0) {
    return (
      <EmptyState
        title="No variables yet"
        description="Variables appear here after a cell defines them"
      />
    );
  }
  return (
    <ul className="divide-y divide-surface-border">
      {variables.map((variable) => (
        <li key={variable.name} className="px-4 py-2">
          <div className="flex items-baseline gap-2">
            <span className="min-w-0 truncate font-mono text-sm text-foreground">
              {variable.name}
            </span>
            <span className="ml-auto shrink-0 font-mono text-xs text-muted-foreground">
              {variable.type}
            </span>
          </div>
          {variable.shape || variable.preview ? (
            <p className="mt-0.5 truncate text-xs text-muted-foreground" title={variable.preview ?? undefined}>
              {[variable.shape, variable.preview].filter(Boolean).join(" · ")}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function PackagesTab({
  notebookId,
  ready,
  packageEvents,
}: {
  notebookId: string;
  ready: boolean;
  packageEvents: NotebookEvent[];
}) {
  const queryClient = useQueryClient();
  const packages = useQuery({
    queryKey: ["notebook-packages", notebookId],
    queryFn: () => notebookApi.packages(notebookId),
  });
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [operation, setOperation] = useState<string | null>(null);

  const log = useMemo(
    () =>
      packageEvents
        .filter((e) => e.event === "package.log" && e.data.operation_id === operation)
        .map((e) => String(e.data.text ?? ""))
        .join(""),
    [operation, packageEvents],
  );

  useEffect(() => {
    if (!operation) return;
    const finished = packageEvents.find(
      (e) => e.event === "package.finished" && e.data.operation_id === operation,
    );
    if (!finished) return;
    setOperation(null);
    void queryClient.invalidateQueries({ queryKey: ["notebook-packages", notebookId] });
    if (finished.data.ok) {
      toast.success(
        `${finished.data.name} ${finished.data.version ?? ""} installed`.trim(),
        finished.data.restart_recommended
          ? { description: "Restart the kernel to use the new version of an already imported package" }
          : undefined,
      );
      setName("");
      setVersion("");
    } else {
      toast.error(String(finished.data.error ?? "The package could not be installed"));
    }
  }, [notebookId, operation, packageEvents, queryClient]);

  const allowed = packages.data?.allowed ?? [];
  const matches = name
    ? allowed.filter((p) => p.name.includes(name.toLowerCase().replace(/_/g, "-"))).slice(0, 8)
    : [];

  async function install(target: string) {
    try {
      const response = await notebookApi.install(notebookId, target, version || undefined);
      setOperation(response.operation_id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The package could not be installed");
    }
  }

  async function remove(target: string) {
    try {
      await notebookApi.removePackage(notebookId, target);
      toast.success(`${target} removed from this notebook`, {
        description: "It stays loaded until the runtime restarts",
      });
      void queryClient.invalidateQueries({ queryKey: ["notebook-packages", notebookId] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The package could not be removed");
    }
  }

  if (packages.isLoading) {
    return <p className="px-4 py-3 text-sm text-muted-foreground">Loading packages</p>;
  }
  if (packages.isError) {
    return (
      <EmptyState
        variant="error"
        title="Packages could not load"
        description="Try again in a moment"
        action={<Button size="sm" variant="outline" onClick={() => void packages.refetch()}>Retry</Button>}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4 px-4 py-3">
      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (name) void install(name);
        }}
      >
        <label className="text-xs text-muted-foreground" htmlFor="notebook-package-name">
          Add an approved package
        </label>
        <div className="flex gap-2">
          <Input
            id="notebook-package-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="scikit-learn"
            className="h-8 font-mono text-sm"
            autoComplete="off"
            disabled={!!operation}
          />
          <Input
            value={version}
            onChange={(event) => setVersion(event.target.value)}
            placeholder="Version"
            aria-label="Version (optional)"
            className="h-8 w-24 font-mono text-sm"
            disabled={!!operation}
          />
        </div>
        {matches.length > 0 && !operation ? (
          <div className="flex flex-wrap gap-1">
            {matches.map((match) => (
              <button
                key={match.name}
                type="button"
                className="rounded border border-surface-border px-1.5 py-0.5 font-mono text-xs text-muted-foreground hover:text-foreground"
                onClick={() => setName(match.name)}
              >
                {match.name}
              </button>
            ))}
          </div>
        ) : null}
        <Button type="submit" size="sm" disabled={!ready || !name || !!operation}>
          {operation ? <Loader2 className="size-4 animate-spin" /> : <Package className="size-4" />}
          {operation ? "Installing" : "Install"}
        </Button>
        {!ready ? (
          <p className="text-xs text-muted-foreground">Start the runtime to install packages</p>
        ) : (
          <p className="text-xs text-muted-foreground">
            Installing runs the package's own setup code. Only packages your administrator approved are available.
          </p>
        )}
        {operation || log ? (
          <pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 font-mono text-xs leading-5 whitespace-pre-wrap text-muted-foreground">
            {log || "Waiting for the runtime"}
          </pre>
        ) : null}
      </form>

      <section aria-labelledby="notebook-packages-requested">
        <h3 id="notebook-packages-requested" className="text-xs font-medium text-foreground">
          This notebook
        </h3>
        {packages.data?.requested.length ? (
          <ul className="mt-1 divide-y divide-surface-border">
            {packages.data.requested.map((item) => (
              <li key={item.name} className="flex items-center gap-2 py-1.5">
                <span className="font-mono text-sm">{item.name}</span>
                <span className="font-mono text-xs text-muted-foreground">
                  {item.installed_version ?? item.specifier ?? ""}
                </span>
                <Button
                  size="icon"
                  variant="ghost"
                  className="ml-auto size-7 text-muted-foreground"
                  aria-label={`Remove ${item.name}`}
                  onClick={() => void remove(item.name)}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-xs text-muted-foreground">
            No added packages. They are installed again whenever the runtime starts.
          </p>
        )}
      </section>

      <section aria-labelledby="notebook-packages-base">
        <h3 id="notebook-packages-base" className="text-xs font-medium text-foreground">
          Included
        </h3>
        <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1">
          {packages.data?.base.map((item) => (
            <li key={item.name} className="flex justify-between font-mono text-xs">
              <span>{item.name}</span>
              <span className="text-muted-foreground">{item.version}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export function NotebookSidePanel({
  tab,
  onTab,
  onClose,
  notebookId,
  variables,
  ready,
  packageEvents,
  overlay,
  outline,
  onJump,
}: {
  tab: SidePanelTab;
  onTab: (tab: SidePanelTab) => void;
  onClose: () => void;
  notebookId: string;
  variables: VariableInfo[] | null;
  ready: boolean;
  packageEvents: NotebookEvent[];
  overlay: boolean;
  outline: OutlineItem[];
  onJump: (cellId: string) => void;
}) {
  return (
    <aside
      aria-label="Notebook tools"
      className={cn(
        "flex w-80 shrink-0 flex-col border-l border-surface-border bg-background",
        overlay && "absolute inset-y-0 right-0 z-30 shadow-lg",
      )}
    >
      <div className="flex h-11 items-center gap-1 border-b border-surface-border px-2">
        {(["outline", "variables", "packages"] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={tab === value}
            onClick={() => onTab(value)}
            className={cn(
              "rounded-md px-2.5 py-1 text-sm transition-colors",
              tab === value ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {value === "outline" ? "Outline" : value === "variables" ? "Variables" : "Packages"}
          </button>
        ))}
        <Button
          size="icon"
          variant="ghost"
          className="ml-auto size-7 text-muted-foreground"
          aria-label="Close notebook tools"
          onClick={onClose}
        >
          <X className="size-4" />
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "outline" ? (
          <OutlineTab items={outline} onJump={onJump} />
        ) : tab === "variables" ? (
          <VariablesTab variables={variables} ready={ready} />
        ) : (
          <PackagesTab notebookId={notebookId} ready={ready} packageEvents={packageEvents} />
        )}
      </div>
    </aside>
  );
}
