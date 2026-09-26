import { useCallback, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { AlertCircle, ArrowLeft, CircleSlash, Workflow } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { StatusBadge } from "@/components/ui/status-badge";
import { fetchGraph } from "./api";
import { formatSchedule, taskRunTone, isAccessError } from "./presentation";
import { RunHistoryDrawer } from "./run-history-drawer";
import { TaskFlow } from "./task-flow";

/**
 * One task as a full page, the way the SQL workspace is: its own header, and a
 * body that owns the whole viewport with no page padding. The flow is the page;
 * the run history floats over its lower edge so the graph stays the subject.
 */
export default function TaskDetail({ graphId }: { graphId: string }) {
  const navigate = useNavigate();
  const bodyRef = useRef<HTMLDivElement>(null);
  // The height the drawer currently occupies, so the flow can centre itself in
  // the space that stays visible rather than behind the drawer.
  const [drawerInset, setDrawerInset] = useState(0);

  const detailQuery = useQuery({
    queryKey: ["task-orchestration", "graph", graphId],
    queryFn: ({ signal }) => fetchGraph(graphId, signal),
  });

  const handleInsetChange = useCallback((inset: number) => {
    setDrawerInset(inset);
  }, []);

  const detail = detailQuery.data;
  const anchor = detail?.nodes.find((node) => !node.is_finalizer);

  if (detailQuery.isError && isAccessError(detailQuery.error)) {
    return (
      <div data-layout="fixed" className="flex h-full min-h-0 flex-col">
        <Header fixed />
        <div className="flex min-h-0 flex-1 items-center justify-center border-t">
          <EmptyState
            icon={CircleSlash}
            title="Task not found or no access"
            description="This task does not exist, or your account cannot read it."
            action={
              <Button
                variant="outline"
                size="sm"
                onClick={() => void navigate({ to: "/tasks" })}
              >
                Back to tasks
              </Button>
            }
          />
        </div>
      </div>
    );
  }

  if (detailQuery.isError) {
    return (
      <div data-layout="fixed" className="flex h-full min-h-0 flex-col">
        <Header fixed />
        <div className="flex min-h-0 flex-1 items-center justify-center border-t">
          <EmptyState
            variant="error"
            icon={AlertCircle}
            title="Could not load this task"
            description="The task API did not respond. Retry to load the task."
            action={
              <Button
                variant="outline"
                size="sm"
                onClick={() => void detailQuery.refetch()}
              >
                Retry
              </Button>
            }
          />
        </div>
      </div>
    );
  }

  return (
    <div data-layout="fixed" className="flex h-full min-h-0 flex-col">
      <Header fixed>
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="shrink-0"
            onClick={() => void navigate({ to: "/tasks" })}
            aria-label="Back to tasks"
          >
            <ArrowLeft className="size-4" />
          </Button>
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold">
              {detail?.graph_id ?? graphId}
            </h1>
            {detail ? (
              <p className="truncate text-sm text-muted-foreground">
                {detail.node_count} {detail.node_count === 1 ? "node" : "nodes"}{" "}
                · {formatSchedule(anchor?.schedule_kind, anchor?.schedule_expr)}
              </p>
            ) : null}
          </div>
          {anchor?.last_state ? (
            <StatusBadge tone={taskRunTone(anchor.last_state)} dot>
              {anchor.last_state}
            </StatusBadge>
          ) : null}
        </div>
        <Workflow
          aria-hidden="true"
          className="size-5 shrink-0 text-muted-foreground"
        />
      </Header>

      <div
        ref={bodyRef}
        data-layout="fixed"
        className="relative min-h-0 flex-1 overflow-hidden border-t"
      >
        <div className="absolute inset-0">
          <TaskFlow
            key={graphId}
            graphId={graphId}
            nodes={detail?.nodes ?? []}
            edges={detail?.edges ?? []}
            isLoading={detailQuery.isLoading}
            isError={detailQuery.isError}
            onRetry={() => void detailQuery.refetch()}
            bottomInset={drawerInset}
          />
        </div>

        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col justify-end">
          <RunHistoryDrawer
            graphId={graphId}
            containerRef={bodyRef}
            onInsetChange={handleInsetChange}
          />
        </div>
      </div>
    </div>
  );
}
