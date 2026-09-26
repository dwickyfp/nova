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
import { fetchTaskSQL, type GraphNode } from "./api";
import { isAccessError } from "./presentation";

export function TaskSQLDialog({
  graphId,
  node,
}: {
  graphId: string;
  node: GraphNode;
}) {
  const [open, setOpen] = useState(false);
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
          aria-label={`View SQL for ${node.name}`}
          title={`View SQL for ${node.name}`}
        >
          <FileCode2 aria-hidden="true" className="size-4" />
          SQL
        </Button>
      </DialogTrigger>
      <DialogContent className="flex max-h-[calc(100%-2rem)] min-w-0 flex-col overflow-hidden sm:max-w-3xl">
        <DialogHeader className="shrink-0 pr-6">
          <DialogTitle>Task SQL</DialogTitle>
          <DialogDescription className="break-all">
            {node.name}
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
                  : "Could not load task SQL"
              }
              description="The SQL definition is unavailable. Retry to load it."
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
          ) : query.data?.sql ? (
            <pre className="whitespace-pre-wrap break-words rounded-md bg-muted p-4 font-mono text-sm">
              <code>{query.data.sql}</code>
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground">
              No SQL definition was recorded for this task.
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
