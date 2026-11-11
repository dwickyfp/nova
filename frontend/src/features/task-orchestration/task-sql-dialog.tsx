import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, FileCode2 } from "lucide-react";
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
import { SqlCodeBlock } from "@/features/database-explorer/sql-code-block";
import { fetchTaskSQL, type GraphNode } from "./api";
import { formatTaskSql, isAccessError } from "./presentation";

export function TaskSQLDialog({
  graphId,
  node,
}: {
  graphId: string;
  node: GraphNode;
}) {
  const [open, setOpen] = useState(false);
  const python = node.language === "python";
  const kind = python ? "Python" : "SQL";
  const query = useQuery({
    queryKey: ["task-orchestration", "sql", graphId, node.task_id],
    queryFn: ({ signal }) => fetchTaskSQL(graphId, node.task_id, signal),
    enabled: open,
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          aria-label={`View ${kind} for ${node.name}`}
          title={`View ${kind} for ${node.name}`}
        >
          <FileCode2 aria-hidden="true" className="size-4" />
          {kind}
        </Button>
      </DialogTrigger>
      <DialogContent className="flex max-h-[calc(100%-2rem)] min-w-0 flex-col overflow-hidden sm:max-w-3xl">
        <DialogHeader className="shrink-0 pr-6">
          <DialogTitle>Task {kind}</DialogTitle>
          <DialogDescription className="break-all">
            {node.name}
            {python && query.data
              ? ` · Python ${query.data.runtime_version ?? ""}`.trimEnd()
              : ""}
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 min-w-0 flex-1 overflow-auto">
          {query.isLoading ? (
            <LoadingLines rows={5} />
          ) : query.isError ? (
            <EmptyState
              icon={AlertCircle}
              variant="error"
              title={
                isAccessError(query.error)
                  ? "Task not found or no access"
                  : `Could not load task ${kind}`
              }
              description={`The ${kind} definition is unavailable. Retry to load it.`}
              action={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void query.refetch()}
                >
                  Retry
                </Button>
              }
            />
          ) : query.data?.sql && python ? (
            <div className="space-y-3">
              {query.data.packages?.length ? (
                <p className="text-xs text-muted-foreground">
                  Packages: {query.data.packages.join(", ")}
                </p>
              ) : null}
              <pre
                aria-label="Task Python code"
                className="overflow-x-auto rounded-lg border border-surface-border bg-surface-1 p-4 font-mono text-xs leading-5"
              >
                <code>{query.data.sql}</code>
              </pre>
            </div>
          ) : query.data?.sql ? (
            <SqlCodeBlock source={formatTaskSql(query.data.sql)} label="Task SQL" />
          ) : (
            <p className="text-sm text-muted-foreground">
              No {kind} definition was recorded for this task.
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
