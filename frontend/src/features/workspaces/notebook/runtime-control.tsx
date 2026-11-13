import { useEffect, useState } from "react";
import { ChevronDown, Power, RotateCcw, Square } from "lucide-react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { notebookApi } from "./api";
import { DOT, isLive, RUNTIME_STATE } from "./helpers";
import type { RuntimeInfo, RuntimeLimits } from "./types";

function useMetrics(notebookId: string, active: boolean) {
  const [metrics, setMetrics] = useState<{ cpu: number; used: number; total: number } | null>(null);
  useEffect(() => {
    if (!active) {
      setMetrics(null);
      return;
    }
    let cancelled = false;
    const read = async () => {
      try {
        const response = await notebookApi.metrics(notebookId);
        if (!cancelled && response.metrics) {
          setMetrics({
            cpu: response.metrics.cpu_percent,
            used: response.metrics.memory_used_mib,
            total: response.metrics.memory_total_mib,
          });
        }
      } catch {
        // Metrics are a convenience; the runtime state comes from events.
      }
    };
    void read();
    const timer = window.setInterval(read, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, notebookId]);
  return metrics;
}

function memoryLabel(mib: number) {
  return mib >= 1024 ? `${(mib / 1024).toFixed(1)} GB` : `${Math.round(mib)} MB`;
}

export function RuntimeControl({
  notebookId,
  runtime,
  limits,
  anyRunning,
  onStart,
  onStop,
  onRestart,
}: {
  notebookId: string;
  runtime: RuntimeInfo;
  limits: RuntimeLimits | null;
  anyRunning: boolean;
  onStart: () => Promise<void>;
  onStop: () => Promise<void>;
  onRestart: () => Promise<void>;
}) {
  const state = RUNTIME_STATE[runtime.status] ?? RUNTIME_STATE.ABSENT;
  const ready = runtime.status === "READY" || runtime.status === "BUSY";
  const metrics = useMetrics(notebookId, ready);
  const [confirm, setConfirm] = useState<"stop" | "restart" | null>(null);
  const [pending, setPending] = useState(false);

  async function act(action: () => Promise<void>, failure: string) {
    setPending(true);
    try {
      await action();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : failure);
    } finally {
      setPending(false);
    }
  }

  const detail =
    runtime.status === "FAILED" || runtime.status === "LOST"
      ? runtime.error
      : !ready && isLive(runtime.status)
        ? runtime.stage
        : null;

  return (
    <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
      {runtime.status === "ABSENT" ||
      runtime.status === "STOPPED" ||
      runtime.status === "FAILED" ||
      runtime.status === "LOST" ? (
        <Button
          size="sm"
          variant="outline"
          disabled={pending}
          onClick={() => void act(onStart, "The runtime could not start")}
        >
          <Power className="size-4" />
          Start runtime
        </Button>
      ) : null}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex h-8 shrink-0 items-center gap-2 rounded-md border border-surface-border px-2.5 text-sm hover:bg-muted"
            aria-label={`Runtime: ${state.label}`}
          >
            <span
              className={cn(
                "size-2 shrink-0 rounded-full",
                DOT[state.tone],
                runtime.status === "BUSY" && "animate-pulse",
              )}
              aria-hidden
            />
            <span className="whitespace-nowrap">{state.label}</span>
            {metrics ? (
              <span className="hidden font-mono text-xs text-muted-foreground tabular-nums @3xl/editor:inline">
                {Math.round(metrics.cpu)}% · {memoryLabel(metrics.used)} of {memoryLabel(metrics.total)}
              </span>
            ) : null}
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-72">
          <DropdownMenuLabel className="font-normal">
            <span className="block text-sm text-foreground">Python and DuckDB runtime</span>
            <span className="block text-xs text-muted-foreground">
              {limits
                ? `${limits.cpu} CPU, ${limits.memory.replace("Gi", " GB")} memory. Stops after ${Math.round(limits.idle_timeout_seconds / 60)} min without a run.`
                : "Isolated for this notebook"}
            </span>
            {runtime.active_role ? (
              <span className="mt-1 block text-xs text-muted-foreground">
                Nova data is read as {runtime.active_role}
                {runtime.nova_attached === false && runtime.nova_error ? `. ${runtime.nova_error}` : ""}
              </span>
            ) : null}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {!isLive(runtime.status) ? (
            <DropdownMenuItem onSelect={() => void act(onStart, "The runtime could not start")}>
              <Power className="size-4" />
              Start runtime
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem
            disabled={!ready}
            onSelect={() => setConfirm("restart")}
          >
            <RotateCcw className="size-4" />
            Restart kernel
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={!isLive(runtime.status)}
            onSelect={() => setConfirm("stop")}
          >
            <Square className="size-4" />
            Stop runtime
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {detail ? (
        <span
          className={cn(
            "hidden min-w-0 truncate text-xs @2xl/editor:inline",
            runtime.status === "FAILED" || runtime.status === "LOST"
              ? "text-destructive"
              : "text-muted-foreground",
          )}
          role="status"
        >
          {detail}
        </span>
      ) : null}
      <ConfirmDialog
        open={confirm === "restart"}
        onOpenChange={(open) => !open && setConfirm(null)}
        title="Restart the kernel?"
        desc="Variables and in-memory DuckDB tables are cleared. Files and installed packages stay. Earlier outputs are marked out of date."
        confirmText="Restart kernel"
        handleConfirm={() => {
          setConfirm(null);
          void act(onRestart, "The kernel could not restart");
        }}
      />
      <ConfirmDialog
        open={confirm === "stop"}
        destructive
        onOpenChange={(open) => !open && setConfirm(null)}
        title="Stop the runtime?"
        desc={
          anyRunning
            ? "A cell is still running and will be stopped. Variables are cleared; files in outputs are saved to the workspace."
            : "Variables are cleared; files in outputs are saved to the workspace. Start it again to keep working."
        }
        confirmText="Stop runtime"
        handleConfirm={() => {
          setConfirm(null);
          void act(onStop, "The runtime could not stop");
        }}
      />
    </div>
  );
}
