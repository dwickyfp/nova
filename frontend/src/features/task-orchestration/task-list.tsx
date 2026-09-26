import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  AlertCircle,
  Play,
  CircleSlash,
  SearchX,
  Workflow,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { PageHeader } from "@/components/ui/page-header";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  SimpleTablePagination,
  SimpleTableToolbar,
  SimpleTableViewport,
} from "@/components/data-table/simple-table-controls";
import { fetchGraphs, runGraph, type GraphSummary } from "./api";
import { RunErrorDialog } from "./run-error-dialog";
import {
  formatSchedule,
  formatTimestamp,
  graphRunTone,
  isAccessError,
} from "./presentation";

const PAGE_SIZE = 10;

function taskLabel(graph: GraphSummary): string {
  return graph.root_task ?? graph.graph_id;
}

function databaseLabel(graph: GraphSummary): string {
  if (!graph.database_name) return "—";
  return graph.schema_name
    ? `${graph.database_name}.${graph.schema_name}`
    : graph.database_name;
}

/**
 * The single Tasks page: every task graph the caller may see, with its run
 * tallies. A row click navigates to the detail page rather than expanding in
 * place — the graph and its run history need the whole viewport.
 */
export default function TaskList() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const graphsQuery = useQuery({
    queryKey: ["task-orchestration", "graphs"],
    queryFn: ({ signal }) => fetchGraphs(signal),
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.graphs.some((graph) =>
        ["pending", "running"].includes(graph.last_run?.state ?? ""),
      )
        ? 2000
        : false,
  });

  const graphs = graphsQuery.data?.graphs ?? [];

  const filtered = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    if (!normalized) return graphs;
    return graphs.filter((graph) =>
      [
        graph.graph_id,
        graph.root_task ?? "",
        graph.database_name ?? "",
        graph.schema_name ?? "",
        graph.owner_role ?? "",
        formatSchedule(graph.schedule_kind, graph.schedule_expr),
      ].some((value) => value.toLowerCase().includes(normalized)),
    );
  }, [graphs, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const paged = filtered.slice(
    (safePage - 1) * PAGE_SIZE,
    safePage * PAGE_SIZE,
  );

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-4">
      <PageHeader
        title="Tasks"
        description="Every task you can see, with its schedule and run tallies. Open a task to inspect its flow and history."
      />

      <SimpleTableToolbar
        search={search}
        onSearchChange={(value) => {
          setSearch(value);
          setPage(1);
        }}
        searchPlaceholder="Search task, database, schedule..."
        resultLabel={`${filtered.length} ${filtered.length === 1 ? "task" : "tasks"}`}
      />

      <div className="min-h-0 min-w-0 flex-1">
        <SimpleTableViewport className="max-h-full h-full">
          <table className="w-full">
            <thead>
              <tr>
                <Th className="text-left">Name</Th>
                <Th className="text-right">Total Run</Th>
                <Th className="text-right">Success</Th>
                <Th className="text-right">Failed</Th>
                <Th className="text-left">Schedule</Th>
                <Th className="text-left">Database</Th>
                <Th className="text-left">Created</Th>
                <Th className="text-left whitespace-nowrap">Last run</Th>
                <Th className="text-right">Action</Th>
              </tr>
            </thead>
            <tbody>
              {graphsQuery.isLoading ? (
                <tr>
                  <td colSpan={9} className="px-4 py-6">
                    <LoadingLines rows={5} />
                  </td>
                </tr>
              ) : graphsQuery.isError ? (
                <tr>
                  <td colSpan={9} className="px-4 py-6">
                    {isAccessError(graphsQuery.error) ? (
                      <EmptyState
                        icon={CircleSlash}
                        title="No access to tasks"
                        description="Your account cannot read these tasks. Ask an administrator for access."
                      />
                    ) : (
                      <EmptyState
                        variant="error"
                        icon={AlertCircle}
                        title="Could not load tasks"
                        description="The task API did not respond. Check the connection and retry."
                        action={
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => void graphsQuery.refetch()}
                          >
                            Retry
                          </Button>
                        }
                      />
                    )}
                  </td>
                </tr>
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-6">
                    <EmptyState
                      icon={search ? SearchX : Workflow}
                      title={
                        search ? "No tasks match this search" : "No tasks yet"
                      }
                      description={
                        search
                          ? "Clear the search to see every task."
                          : "A task appears here once it is created with CREATE TASK."
                      }
                      action={
                        search ? (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setSearch("")}
                          >
                            Clear search
                          </Button>
                        ) : undefined
                      }
                    />
                  </td>
                </tr>
              ) : (
                paged.map((graph) => (
                  <TaskRow
                    key={graph.graph_id}
                    graph={graph}
                    onOpen={() =>
                      void navigate({
                        to: "/tasks/$graphId",
                        params: { graphId: graph.graph_id },
                      })
                    }
                  />
                ))
              )}
            </tbody>
          </table>
        </SimpleTableViewport>
      </div>

      <SimpleTablePagination
        page={safePage}
        pageSize={PAGE_SIZE}
        total={filtered.length}
        onPageChange={setPage}
        onPageSizeChange={() => {}}
      />
    </div>
  );
}

function Th({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <th
      scope="col"
      className={cn(
        "px-4 py-3 text-xs font-medium text-muted-foreground",
        className,
      )}
    >
      {children}
    </th>
  );
}

function TaskRow({
  graph,
  onOpen,
}: {
  graph: GraphSummary;
  onOpen: () => void;
}) {
  const counts = graph.run_counts;
  const queryClient = useQueryClient();
  const runMutation = useMutation({
    mutationFn: () => runGraph(graph.graph_id),
    onSuccess: () => {
      toast.success(`Run queued for ${taskLabel(graph)}`);
      void queryClient.invalidateQueries({ queryKey: ["task-orchestration"] });
    },
    onError: (error: Error) =>
      toast.error(error.message || "Could not run task"),
  });
  return (
    <tr
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      role="link"
      tabIndex={0}
      className="cursor-pointer border-b border-border transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
    >
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <Workflow
            aria-hidden="true"
            className="size-4 shrink-0 text-muted-foreground"
          />
          <div className="min-w-0">
            <span className="block truncate text-sm font-medium">
              {taskLabel(graph)}
            </span>
            <span className="block text-xs text-muted-foreground">
              {graph.owner_role ?? "Owner role required"}
            </span>
          </div>
          {graph.last_run ? (
            <StatusBadge tone={graphRunTone(graph.last_run.state)} dot>
              {graph.last_run.state}
            </StatusBadge>
          ) : (
            <StatusBadge tone="neutral">never run</StatusBadge>
          )}
        </div>
      </td>
      <td className="px-4 py-3 text-right text-sm tabular-nums">
        {counts.total}
      </td>
      <td className="px-4 py-3 text-right text-sm tabular-nums text-success-strong">
        {counts.success}
      </td>
      <td
        className={cn(
          "px-4 py-3 text-right text-sm tabular-nums",
          counts.failed > 0 ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {counts.failed}
      </td>
      <td className="px-4 py-3 text-sm text-muted-foreground">
        {formatSchedule(graph.schedule_kind, graph.schedule_expr)}
      </td>
      <td className="px-4 py-3 text-sm text-muted-foreground">
        {databaseLabel(graph)}
      </td>
      <td className="px-4 py-3 text-sm text-muted-foreground whitespace-nowrap">
        {formatTimestamp(graph.created_at)}
      </td>
      <td className="px-4 py-3 text-sm text-muted-foreground whitespace-nowrap">
        {graph.last_run
          ? formatTimestamp(graph.last_run.started_at)
          : "Never run"}
      </td>
      <td className="px-4 py-3 text-right">
        <div className="flex items-center justify-end gap-2">
          {graph.last_run?.state === "failed" ? (
            <RunErrorDialog
              runId={graph.last_run.id}
              taskName={taskLabel(graph)}
            />
          ) : null}
          <Button
            variant="outline"
            size="sm"
            aria-label={`Run ${taskLabel(graph)}`}
            disabled={runMutation.isPending || !graph.can_run}
            title={
              graph.can_run
                ? undefined
                : graph.owner_role
                  ? `Activate ${graph.owner_role} to run`
                  : "Assign one owner role to all nodes before running"
            }
            onClick={(event) => {
              event.stopPropagation();
              runMutation.mutate();
            }}
          >
            <Play aria-hidden="true" className="size-3.5" />
            {runMutation.isPending ? "Starting..." : "Run"}
          </Button>
        </div>
      </td>
    </tr>
  );
}
