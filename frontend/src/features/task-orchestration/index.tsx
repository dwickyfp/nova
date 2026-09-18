import { useMemo, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  CircleSlash,
  GitFork,
  ListTree,
  SearchX,
  Workflow,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { LoadingLines, RefreshBanner } from '@/components/ui/loading-overlay';
import { PageHeader } from '@/components/ui/page-header';
import { StatusBadge } from '@/components/ui/status-badge';
import {
  SimpleTablePagination,
  SimpleTableToolbar,
  SimpleTableViewport,
} from '@/components/data-table/simple-table-controls';
import {
  fetchGraph,
  fetchGraphRun,
  fetchGraphRuns,
  fetchGraphs,
  type GraphEdge,
  type GraphNode,
  type GraphRunResponse,
  type GraphSummary,
  type NodeRunResponse,
} from './api';
import {
  formatDuration,
  formatSchedule,
  formatTimestamp,
  graphRunTone,
  isAccessError,
  taskRunTone,
} from './presentation';

const GRAPH_PAGE_SIZE = 8;

export default function TaskOrchestration() {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [selectedGraphId, setSelectedGraphId] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const graphsQuery = useQuery({
    queryKey: ['task-orchestration', 'graphs'],
    queryFn: ({ signal }) => fetchGraphs(signal),
    placeholderData: keepPreviousData,
  });

  const graphs = graphsQuery.data?.graphs ?? [];

  const filtered = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    if (!normalized) return graphs;
    return graphs.filter((graph) =>
      [graph.graph_id, graph.root_task ?? '', graph.schedule_expr ?? ''].some(
        (value) => value.toLowerCase().includes(normalized),
      ),
    );
  }, [graphs, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / GRAPH_PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const paged = filtered.slice(
    (safePage - 1) * GRAPH_PAGE_SIZE,
    safePage * GRAPH_PAGE_SIZE,
  );

  const hasGraphs = filtered.length > 0;

  function handleSelectGraph(graphId: string) {
    setSelectedGraphId(graphId);
    setSelectedRunId(null);
  }

  return (
    <div className='space-y-6'>
      <PageHeader
        title='Task Graphs'
        description='Graphs created with CREATE TASK: their dependencies, finalizers, and run history.'
      />

      <div className='grid gap-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]'>
        <section aria-label='Task graphs' className='min-w-0 space-y-4'>
          <SimpleTableToolbar
            search={search}
            onSearchChange={(value) => {
              setSearch(value);
              setPage(1);
            }}
            searchPlaceholder='Search graph, root task, schedule...'
            resultLabel={`${filtered.length} ${filtered.length === 1 ? 'graph' : 'graphs'}`}
          />

          <SimpleTableViewport>
            {graphsQuery.isFetching && !graphsQuery.isLoading ? (
              <RefreshBanner label='Loading graphs...' />
            ) : null}
            {graphsQuery.isLoading ? (
              <div className='p-4'>
                <LoadingLines rows={5} />
              </div>
            ) : graphsQuery.isError ? (
              <div className='p-4'>
                {isAccessError(graphsQuery.error) ? (
                  <EmptyState
                    icon={CircleSlash}
                    title='No access to task graphs'
                    description='Your account cannot read these graphs. Ask an administrator for access.'
                  />
                ) : (
                  <EmptyState
                    variant='error'
                    icon={AlertCircle}
                    title='Could not load task graphs'
                    description='The orchestration API did not respond. Check the connection and retry.'
                    action={
                      <Button
                        variant='outline'
                        size='sm'
                        onClick={() => void graphsQuery.refetch()}
                      >
                        Retry
                      </Button>
                    }
                  />
                )}
              </div>
            ) : !hasGraphs ? (
              <div className='p-4'>
                <EmptyState
                  icon={search ? SearchX : GitFork}
                  title={
                    search
                      ? 'No graphs match this search'
                      : 'No task graphs yet'
                  }
                  description={
                    search
                      ? 'Clear the search to see every graph.'
                      : 'A graph appears here once a task is created with CREATE TASK ... AFTER.'
                  }
                  action={
                    search ? (
                      <Button
                        variant='outline'
                        size='sm'
                        onClick={() => setSearch('')}
                      >
                        Clear search
                      </Button>
                    ) : undefined
                  }
                />
              </div>
            ) : (
              <ul className='divide-y divide-border'>
                {paged.map((graph) => (
                  <GraphListItem
                    key={graph.graph_id}
                    graph={graph}
                    selected={graph.graph_id === selectedGraphId}
                    onSelect={() => handleSelectGraph(graph.graph_id)}
                  />
                ))}
              </ul>
            )}
          </SimpleTableViewport>

          <SimpleTablePagination
            page={safePage}
            pageSize={GRAPH_PAGE_SIZE}
            total={filtered.length}
            onPageChange={setPage}
            onPageSizeChange={() => {}}
          />
        </section>

        <section className='min-w-0' aria-label='Graph detail'>
          {selectedGraphId ? (
            <GraphDetail
              graphId={selectedGraphId}
              selectedRunId={selectedRunId}
              onSelectRun={setSelectedRunId}
            />
          ) : (
            <EmptyState
              icon={GitFork}
              title='Select a graph'
              description='Pick a graph on the left to see its nodes, dependency and finalizer edges, and run history.'
              className='h-full'
            />
          )}
        </section>
      </div>
    </div>
  );
}

function GraphListItem({
  graph,
  selected,
  onSelect,
}: {
  graph: GraphSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type='button'
        onClick={onSelect}
        aria-current={selected ? 'true' : undefined}
        className={cn(
          'flex w-full min-w-0 items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50',
          selected && 'bg-muted/60',
        )}
      >
        <Workflow
          aria-hidden='true'
          className={cn(
            'size-4 shrink-0',
            selected ? 'text-primary' : 'text-muted-foreground',
          )}
        />
        <span className='min-w-0 flex-1'>
          <span className='flex items-center gap-2'>
            <span className='truncate text-sm font-medium'>
              {graph.root_task ?? graph.graph_id}
            </span>
            {graph.last_run ? (
              <StatusBadge tone={graphRunTone(graph.last_run.state)} dot>
                {graph.last_run.state}
              </StatusBadge>
            ) : (
              <StatusBadge tone='neutral'>never run</StatusBadge>
            )}
          </span>
          <span className='mt-0.5 block truncate text-xs text-muted-foreground'>
            {graph.node_count} {graph.node_count === 1 ? 'node' : 'nodes'} ·{' '}
            {formatSchedule(graph.schedule_kind, graph.schedule_expr)}
          </span>
        </span>
        <ChevronRight
          aria-hidden='true'
          className='size-4 shrink-0 text-muted-foreground'
        />
      </button>
    </li>
  );
}

function GraphDetail({
  graphId,
  selectedRunId,
  onSelectRun,
}: {
  graphId: string;
  selectedRunId: string | null;
  onSelectRun: (runId: string | null) => void;
}) {
  const detailQuery = useQuery({
    queryKey: ['task-orchestration', 'graph', graphId],
    queryFn: ({ signal }) => fetchGraph(graphId, signal),
  });

  if (detailQuery.isLoading) {
    return (
      <div className='rounded-lg border border-border p-4'>
        <LoadingLines rows={6} />
      </div>
    );
  }

  if (detailQuery.isError) {
    return isAccessError(detailQuery.error) ? (
      <EmptyState
        icon={CircleSlash}
        title='Graph not found or no access'
        description='This graph does not exist, or your account cannot read it. Select another graph.'
      />
    ) : (
      <EmptyState
        variant='error'
        icon={AlertCircle}
        title='Could not load this graph'
        description='The orchestration API did not respond. Retry to load the graph definition.'
        action={
          <Button
            variant='outline'
            size='sm'
            onClick={() => void detailQuery.refetch()}
          >
            Retry
          </Button>
        }
      />
    );
  }

  const detail = detailQuery.data;
  if (!detail) return null;

  const finalizerEdges = detail.edges.filter(
    (edge) => edge.edge_kind === 'finalize',
  );
  const dependencyEdges = detail.edges.filter(
    (edge) => edge.edge_kind !== 'finalize',
  );

  return (
    <div className='space-y-6'>
      <div className='flex flex-wrap items-baseline justify-between gap-2'>
        <h2 className='min-w-0 break-all text-base font-medium'>
          {detail.graph_id}
        </h2>
        <span className='text-sm text-muted-foreground'>
          {detail.node_count} {detail.node_count === 1 ? 'node' : 'nodes'}
        </span>
      </div>

      <div>
        <h3 className='mb-2 text-sm font-medium'>Nodes</h3>
        <NodeList nodes={detail.nodes} />
      </div>

      <div>
        <h3 className='mb-2 flex items-center gap-2 text-sm font-medium'>
          Edges
          {finalizerEdges.length > 0 ? (
            <span className='text-xs font-normal text-muted-foreground'>
              {dependencyEdges.length} dependency · {finalizerEdges.length}{' '}
              finalizer
            </span>
          ) : null}
        </h3>
        <EdgeList edges={detail.edges} />
      </div>

      <div>
        <h3 className='mb-2 text-sm font-medium'>Run history</h3>
        <RunHistory
          graphId={graphId}
          selectedRunId={selectedRunId}
          onSelectRun={onSelectRun}
        />
      </div>

      {selectedRunId ? (
        <NodeRunDetail
          runId={selectedRunId}
          onClose={() => onSelectRun(null)}
        />
      ) : null}
    </div>
  );
}

function NodeList({ nodes }: { nodes: GraphNode[] }) {
  if (nodes.length === 0) {
    return (
      <EmptyState
        icon={GitFork}
        title='This graph has no nodes'
        description='The orchestration metadata lists no tasks for this graph id.'
      />
    );
  }

  return (
    <ul className='overflow-hidden rounded-lg border border-border divide-y divide-border'>
      {nodes.map((node) => (
        <li
          key={node.task_id}
          className='flex flex-wrap items-center gap-2 px-3 py-2'
        >
          <span className='min-w-0 flex-1 truncate text-sm'>{node.name}</span>
          {node.is_finalizer ? (
            <StatusBadge tone='info'>finalizer</StatusBadge>
          ) : null}
          <span className='text-xs text-muted-foreground'>
            {formatSchedule(node.schedule_kind, node.schedule_expr)}
          </span>
          {node.last_state ? (
            <StatusBadge tone={taskRunTone(node.last_state)}>
              {node.last_state}
            </StatusBadge>
          ) : (
            <StatusBadge tone='neutral'>no runs</StatusBadge>
          )}
        </li>
      ))}
    </ul>
  );
}

function EdgeList({ edges }: { edges: GraphEdge[] }) {
  if (edges.length === 0) {
    return (
      <EmptyState
        icon={GitFork}
        title='No edges recorded'
        description='This graph has no AFTER or FINALIZE dependencies between its nodes.'
      />
    );
  }

  return (
    <ul className='overflow-hidden rounded-lg border border-border divide-y divide-border'>
      {edges.map((edge, index) => (
        <li
          key={`${edge.parent_task}->${edge.child_task}-${index}`}
          className='flex flex-wrap items-center gap-2 px-3 py-2 text-sm'
        >
          <span className='truncate'>{edge.parent_task}</span>
          <ChevronRight
            aria-hidden='true'
            className='size-3.5 text-muted-foreground'
          />
          <span className='truncate'>{edge.child_task}</span>
          <span className='ml-auto'>
            {edge.edge_kind === 'finalize' ? (
              <StatusBadge tone='info'>FINALIZE</StatusBadge>
            ) : (
              <StatusBadge tone='neutral'>AFTER</StatusBadge>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

function RunHistory({
  graphId,
  selectedRunId,
  onSelectRun,
}: {
  graphId: string;
  selectedRunId: string | null;
  onSelectRun: (runId: string | null) => void;
}) {
  const runsQuery = useQuery({
    queryKey: ['task-orchestration', 'graph-runs', graphId],
    queryFn: ({ signal }) => fetchGraphRuns(graphId, signal),
    placeholderData: keepPreviousData,
  });

  if (runsQuery.isLoading) {
    return <LoadingLines rows={3} />;
  }

  if (runsQuery.isError) {
    return isAccessError(runsQuery.error) ? (
      <EmptyState
        icon={CircleSlash}
        title='Run history not available'
        description='You do not have access to this graph run history.'
      />
    ) : (
      <EmptyState
        variant='error'
        icon={AlertCircle}
        title='Could not load run history'
        description='The orchestration API did not respond. Retry to load the runs.'
        action={
          <Button
            variant='outline'
            size='sm'
            onClick={() => void runsQuery.refetch()}
          >
            Retry
          </Button>
        }
      />
    );
  }

  const runs = runsQuery.data?.runs ?? [];
  if (runs.length === 0) {
    return (
      <EmptyState
        icon={ListTree}
        title='No runs recorded yet'
        description='A run appears here after the graph fires manually, on schedule, or from a stream.'
      />
    );
  }

  return (
    <ul className='overflow-hidden rounded-lg border border-border divide-y divide-border'>
      {runs.map((run) => (
        <li key={run.id}>
          <button
            type='button'
            onClick={() =>
              onSelectRun(selectedRunId === run.id ? null : run.id)
            }
            aria-expanded={selectedRunId === run.id}
            className={cn(
              'flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted/50',
              selectedRunId === run.id && 'bg-muted/60',
            )}
          >
            <StatusBadge tone={graphRunTone(run.state)} dot>
              {run.state}
            </StatusBadge>
            <span className='text-xs text-muted-foreground'>
              {run.trigger_type} · {run.overlap_policy}
            </span>
            <span className='text-xs text-muted-foreground'>
              {formatTimestamp(run.started_at)}
            </span>
            <span className='ml-auto text-xs text-muted-foreground'>
              {formatDuration(run.started_at, run.finished_at)}
            </span>
            {selectedRunId === run.id ? (
              <ChevronLeft
                aria-hidden='true'
                className='size-3.5 rotate-90 text-muted-foreground'
              />
            ) : (
              <ChevronRight
                aria-hidden='true'
                className='size-3.5 text-muted-foreground'
              />
            )}
          </button>
        </li>
      ))}
    </ul>
  );
}

function NodeRunDetail({
  runId,
  onClose,
}: {
  runId: string;
  onClose: () => void;
}) {
  const runQuery = useQuery({
    queryKey: ['task-orchestration', 'run', runId],
    queryFn: ({ signal }) => fetchGraphRun(runId, signal),
  });

  return (
    <div className='rounded-lg border border-border'>
      <div className='flex items-center justify-between border-b border-border px-3 py-2'>
        <h3 className='text-sm font-medium'>Run nodes</h3>
        <Button variant='ghost' size='sm' onClick={onClose}>
          Close
        </Button>
      </div>

      <div className='p-3'>
        {runQuery.isLoading ? (
          <LoadingLines rows={4} />
        ) : runQuery.isError ? (
          isAccessError(runQuery.error) ? (
            <EmptyState
              icon={CircleSlash}
              title='Run not found or no access'
              description='This run does not exist, or your account cannot read it.'
            />
          ) : (
            <EmptyState
              variant='error'
              icon={AlertCircle}
              title='Could not load this run'
              description='The orchestration API did not respond. Retry to load the node runs.'
              action={
                <Button
                  variant='outline'
                  size='sm'
                  onClick={() => void runQuery.refetch()}
                >
                  Retry
                </Button>
              }
            />
          )
        ) : (
          <NodeRunList
            run={runQuery.data?.run ?? null}
            nodes={runQuery.data?.node_runs ?? []}
          />
        )}
      </div>
    </div>
  );
}

function NodeRunList({
  run,
  nodes,
}: {
  run: GraphRunResponse | null;
  nodes: NodeRunResponse[];
}) {
  return (
    <div className='space-y-3'>
      {run ? (
        <div className='flex flex-wrap gap-4 text-xs text-muted-foreground'>
          <span>
            <span className='font-medium text-foreground'>Run:</span>{' '}
            <code className='rounded bg-muted px-1 py-0.5 font-mono'>
              {run.id}
            </code>
          </span>
          <span>
            <span className='font-medium text-foreground'>Trigger:</span>{' '}
            {run.trigger_type}
          </span>
          <span>
            <span className='font-medium text-foreground'>Overlap:</span>{' '}
            {run.overlap_policy}
          </span>
          <span>
            <span className='font-medium text-foreground'>Duration:</span>{' '}
            {formatDuration(run.started_at, run.finished_at)}
          </span>
        </div>
      ) : null}

      {nodes.length === 0 ? (
        <EmptyState
          icon={ListTree}
          title='No node runs for this run'
          description='The graph run has no recorded node executions yet.'
        />
      ) : (
        <ul className='space-y-3'>
          {nodes.map((node) => (
            <li key={node.id} className='rounded-md border border-border p-3'>
              <div className='flex flex-wrap items-center gap-2'>
                <span className='min-w-0 flex-1 truncate text-sm font-medium'>
                  {node.task_id ?? node.id}
                </span>
                <StatusBadge tone={taskRunTone(node.state)}>
                  {node.state}
                </StatusBadge>
                <StatusBadge tone={node.delegated ? 'primary' : 'neutral'}>
                  {node.delegated ? 'delegated' : 'local'}
                </StatusBadge>
              </div>
              <dl className='mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-4'>
                <div>
                  <dt className='font-medium text-foreground'>Attempt</dt>
                  <dd>{node.attempt}</dd>
                </div>
                <div>
                  <dt className='font-medium text-foreground'>Started</dt>
                  <dd>{formatTimestamp(node.started_at)}</dd>
                </div>
                <div>
                  <dt className='font-medium text-foreground'>Finished</dt>
                  <dd>{formatTimestamp(node.finished_at)}</dd>
                </div>
                <div>
                  <dt className='font-medium text-foreground'>Duration</dt>
                  <dd>{formatDuration(node.started_at, node.finished_at)}</dd>
                </div>
              </dl>
              {node.error_message ? (
                <div className='mt-2'>
                  <p className='mb-1 flex items-center gap-1.5 text-xs font-medium text-destructive'>
                    <AlertCircle aria-hidden='true' className='size-3' />
                    Error (redacted server-side)
                  </p>
                  <pre className='overflow-x-auto whitespace-pre-wrap rounded-md bg-destructive/10 p-2 text-xs font-mono text-destructive'>
                    {node.error_message}
                  </pre>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
