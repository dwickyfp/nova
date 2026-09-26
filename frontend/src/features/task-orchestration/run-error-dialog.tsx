import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { fetchGraphRun } from "./api";
import { formatTimestamp, isAccessError } from "./presentation";

export function RunErrorDialog({
  runId,
  taskName,
}: {
  runId: string;
  taskName: string;
}) {
  const [open, setOpen] = useState(false);
  const runQuery = useQuery({
    queryKey: ["task-orchestration", "run", runId],
    queryFn: ({ signal }) => fetchGraphRun(runId, signal),
    enabled: open,
  });
  const failures =
    runQuery.data?.node_runs.filter(
      (node) =>
        node.error_message ||
        node.state === "failed" ||
        node.state === "abandoned",
    ) ?? [];

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="text-destructive"
          onClick={(event) => event.stopPropagation()}
        >
          <AlertCircle aria-hidden="true" className="size-3.5" />
          View error
        </Button>
      </DialogTrigger>
      <DialogContent
        className="flex max-h-[calc(100%-2rem)] min-w-0 flex-col overflow-hidden sm:max-w-2xl"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
      >
        <DialogHeader className="shrink-0 pr-6">
          <DialogTitle>Run error</DialogTitle>
          <DialogDescription className="break-all">
            {taskName}
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          {runQuery.isLoading ? (
            <LoadingLines rows={3} />
          ) : runQuery.isError ? (
            <EmptyState
              variant="error"
              icon={AlertCircle}
              title={
                isAccessError(runQuery.error)
                  ? "Run not found or no access"
                  : "Could not load the error"
              }
              description={
                isAccessError(runQuery.error)
                  ? "This run is unavailable or your account cannot read it."
                  : "Retry to load the error details for this run."
              }
              action={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void runQuery.refetch()}
                >
                  Retry
                </Button>
              }
            />
          ) : (
            <div className="space-y-4">
              <p className="text-xs text-muted-foreground">
                Started {formatTimestamp(runQuery.data?.run.started_at)}
              </p>
              {failures.length ? (
                failures.map((node) => (
                  <section key={node.id} className="space-y-2">
                    <h3 className="break-all text-sm font-medium">
                      {node.task_id ?? node.id} · Attempt {node.attempt}
                    </h3>
                    <pre className="whitespace-pre-wrap break-all rounded-md bg-destructive/10 p-3 font-mono text-xs text-destructive">
                      {node.error_message ||
                        "This step failed without a recorded error message."}
                    </pre>
                  </section>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">
                  This run failed without a recorded error message. Check the
                  task worker logs for more details.
                </p>
              )}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
