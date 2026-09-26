import { Fragment, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  CircleSlash,
  ListTree,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  SimpleTablePagination,
  SimpleTableViewport,
} from "@/components/data-table/simple-table-controls";
import { fetchGraphRun, fetchGraphRunsPage } from "./api";
import { RunErrorDialog } from "./run-error-dialog";
import {
  formatDuration,
  formatTimestamp,
  graphRunTone,
  isAccessError,
  taskRunTone,
} from "./presentation";

const PAGE_SIZE = 10;

/**
 * Paginated run history for one graph. The page is server-side: the request
 * carries `limit`/`offset`, and the response's `count` (the full history size)
 * drives the controls, so a graph with thousands of runs never loads them all.
 */
export function RunHistory({ graphId }: { graphId: string }) {
  const [page, setPage] = useState(1);
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);

  const runsQuery = useQuery({
    queryKey: ["task-orchestration", "graph-runs", graphId, page],
    queryFn: ({ signal }) =>
      fetchGraphRunsPage(
        graphId,
        { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE },
        signal,
      ),
    placeholderData: keepPreviousData,
  });

  const runs = runsQuery.data?.runs ?? [];
  const total = runsQuery.data?.count ?? 0;

  if (runsQuery.isError) {
    return isAccessError(runsQuery.error) ? (
      <EmptyState
        icon={CircleSlash}
        title="Run history not available"
        description="You do not have access to this task run history."
      />
    ) : (
      <EmptyState
        variant="error"
        icon={AlertCircle}
        title="Could not load run history"
        description="The task API did not respond. Retry to load the runs."
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => void runsQuery.refetch()}
          >
            Retry
          </Button>
        }
      />
    );
  }

  if (!runsQuery.isLoading && runs.length === 0) {
    return (
      <EmptyState
        icon={ListTree}
        title="No runs recorded yet"
        description="A run appears here after the task fires manually, on schedule, or from a stream."
      />
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="relative min-h-0 flex-1">
        <SimpleTableViewport className="max-h-full h-full">
          <table className="w-full">
            <thead>
              <tr>
                <th className="w-8" />
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                  Started
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                  Finished
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                  State
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                  Trigger
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium text-muted-foreground">
                  Duration
                </th>
              </tr>
            </thead>
            <tbody>
              {runsQuery.isLoading ? (
                <tr>
                  <td colSpan={6} className="px-4 py-6">
                    <LoadingLines rows={4} />
                  </td>
                </tr>
              ) : (
                runs.map((run) => {
                  const expanded = expandedRunId === run.id;
                  return (
                    <Fragment key={run.id}>
                      <tr
                        onClick={() =>
                          setExpandedRunId(expanded ? null : run.id)
                        }
                        className={cn(
                          "cursor-pointer border-b border-border transition-colors hover:bg-muted/50",
                          expanded && "bg-muted/30",
                        )}
                      >
                        <td className="px-2 py-3 text-muted-foreground">
                          {expanded ? (
                            <ChevronDown
                              aria-hidden="true"
                              className="size-4"
                            />
                          ) : (
                            <ChevronRight
                              aria-hidden="true"
                              className="size-4"
                            />
                          )}
                        </td>
                        <td className="px-4 py-3 text-sm whitespace-nowrap">
                          {formatTimestamp(run.started_at)}
                        </td>
                        <td className="px-4 py-3 text-sm text-muted-foreground whitespace-nowrap">
                          {formatTimestamp(run.finished_at)}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <StatusBadge tone={graphRunTone(run.state)} dot>
                              {run.state}
                            </StatusBadge>
                            {run.state === "failed" ? (
                              <RunErrorDialog
                                runId={run.id}
                                taskName={graphId}
                              />
                            ) : null}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm text-muted-foreground">
                          {run.trigger_type}
                        </td>
                        <td className="px-4 py-3 text-right text-sm text-muted-foreground whitespace-nowrap">
                          {formatDuration(run.started_at, run.finished_at)}
                        </td>
                      </tr>
                      {expanded ? (
                        <tr className="border-b border-border bg-muted/20">
                          <td colSpan={6} className="px-4 py-4">
                            <RunNodes runId={run.id} />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </SimpleTableViewport>
      </div>

      <SimpleTablePagination
        page={page}
        pageSize={PAGE_SIZE}
        total={total}
        onPageChange={(next) => {
          setPage(next);
          setExpandedRunId(null);
        }}
        onPageSizeChange={() => {}}
      />
    </div>
  );
}

function RunNodes({ runId }: { runId: string }) {
  const runQuery = useQuery({
    queryKey: ["task-orchestration", "run", runId],
    queryFn: ({ signal }) => fetchGraphRun(runId, signal),
  });

  if (runQuery.isLoading) return <LoadingLines rows={3} />;
  if (runQuery.isError) {
    return (
      <EmptyState
        variant="error"
        icon={AlertCircle}
        title="Could not load this run"
        description="The task API did not respond. Retry to load the node runs."
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
    );
  }

  const nodes = runQuery.data?.node_runs ?? [];
  if (nodes.length === 0) {
    return (
      <EmptyState
        icon={ListTree}
        title="No node runs for this run"
        description="The graph run has no recorded node executions yet."
      />
    );
  }

  return (
    <div className="space-y-3">
      {runQuery.data?.run.execution_user ? (
        <p className="break-words text-sm text-muted-foreground">
          Executed as {runQuery.data.run.execution_user}
          {runQuery.data.run.execution_role
            ? ` · ${runQuery.data.run.execution_role}`
            : ""}
        </p>
      ) : null}
      <ul className="space-y-3">
        {nodes.map((node) => (
          <li
            key={node.id}
            className="rounded-md border border-border bg-background p-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium">
                {node.task_id ?? node.id}
              </span>
              <StatusBadge tone={taskRunTone(node.state)}>
                {node.state}
              </StatusBadge>
              <StatusBadge tone={node.delegated ? "primary" : "neutral"}>
                {node.delegated ? "delegated" : "local"}
              </StatusBadge>
            </div>
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-4">
              <div>
                <dt className="font-medium text-foreground">Attempt</dt>
                <dd>{node.attempt}</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Started</dt>
                <dd>{formatTimestamp(node.started_at)}</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Finished</dt>
                <dd>{formatTimestamp(node.finished_at)}</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Duration</dt>
                <dd>{formatDuration(node.started_at, node.finished_at)}</dd>
              </div>
            </dl>
            {node.error_message ? (
              <div className="mt-2">
                <p className="mb-1 flex items-center gap-1.5 text-xs font-medium text-destructive">
                  <AlertCircle aria-hidden="true" className="size-3" />
                  Error
                </p>
                <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-destructive/10 p-2 text-xs font-mono text-destructive">
                  {node.error_message}
                </pre>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
