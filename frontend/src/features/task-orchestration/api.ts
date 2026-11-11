import { api } from "@/lib/api-client";

/**
 * Read API for Nova task orchestration (`CREATE TASK` graphs). This is a
 * different surface from `features/tasks-manager/api.ts`: that module reads
 * StarRocks' native `information_schema.tasks`, this one reads Nova's
 * `CONFIG_TASK*` metadata through `/api/v1/task-orchestration`.
 *
 * The backend enforces ownership for reads and manual runs. A
 * graph or run the caller may not see answers `404`, which callers must render
 * as "no access", not as an application error.
 */

export const ORCHESTRATION_GRAPH_RUN_STATES = [
  "pending",
  "running",
  "success",
  "failed",
  "cancelled",
] as const;

export const ORCHESTRATION_TASK_RUN_STATES = [
  "pending",
  "running",
  "success",
  "failed",
  "skipped",
  "abandoned",
] as const;

export const ORCHESTRATION_TRIGGER_TYPES = [
  "manual",
  "schedule",
  "stream",
  "reconcile",
] as const;

export const ORCHESTRATION_OVERLAP_POLICIES = [
  "skip",
  "queue",
  "allow",
] as const;

export const ORCHESTRATION_SCHEDULE_KINDS = [
  "manual",
  "interval",
  "cron",
  "stream",
] as const;

export const ORCHESTRATION_EDGE_KINDS = ["after", "finalize"] as const;

export type GraphRunState = (typeof ORCHESTRATION_GRAPH_RUN_STATES)[number];
export type TaskRunState = (typeof ORCHESTRATION_TASK_RUN_STATES)[number];
export type TriggerType = (typeof ORCHESTRATION_TRIGGER_TYPES)[number];
export type OverlapPolicy = (typeof ORCHESTRATION_OVERLAP_POLICIES)[number];
export type ScheduleKind = (typeof ORCHESTRATION_SCHEDULE_KINDS)[number];
export type EdgeKind = (typeof ORCHESTRATION_EDGE_KINDS)[number];

export interface GraphRunSummary {
  id: string;
  state: GraphRunState;
  trigger_type: TriggerType;
  overlap_policy: OverlapPolicy;
  started_at: string | null;
  finished_at: string | null;
}

/**
 * Run tallies for one graph. `total` is not necessarily `success + failed`:
 * a `cancelled` run counts in `total` but in neither bucket.
 */
export interface RunCounts {
  total: number;
  success: number;
  failed: number;
}

export interface GraphSummary {
  owner_role?: string | null;
  can_run?: boolean;
  graph_id: string;
  root_task: string | null;
  database_name: string | null;
  schema_name: string | null;
  node_count: number;
  schedule_kind: ScheduleKind | null;
  schedule_expr: string | null;
  timezone: string | null;
  overlap_policy: OverlapPolicy;
  last_run: GraphRunSummary | null;
  run_counts: RunCounts;
  created_at: string | null;
  /** `SUSPENDED` graphs run only when someone runs them (Run, EXECUTE TASK). */
  lifecycle_state?: LifecycleState;
}

export type LifecycleState = "STARTED" | "SUSPENDED";

export interface GraphListResponse {
  graphs: GraphSummary[];
  count: number;
}

export interface GraphNode {
  name: string;
  task_id: string;
  schedule_kind: ScheduleKind;
  schedule_expr: string | null;
  timezone: string | null;
  overlap_policy: OverlapPolicy;
  when_expr: string | null;
  created_by: string | null;
  is_finalizer: boolean;
  last_state: TaskRunState | null;
  /** `sql` (one statement or a script) or `python`. */
  language?: TaskLanguage;
  /** Statements in a SQL script node; 1 for a single statement. */
  statement_count?: number;
  /** A Python node's `name[==version]` packages. */
  packages?: string[];
  runtime_version?: string | null;
}

export type TaskLanguage = "sql" | "python";

export interface TaskCode {
  task_id: string;
  name: string;
  sql: string | null;
  language?: TaskLanguage;
  packages?: string[];
  runtime_version?: string | null;
}

export interface GraphEdge {
  parent_task: string;
  child_task: string;
  edge_kind: EdgeKind;
}

export interface GraphDetailResponse {
  graph_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  node_count: number;
}

export interface GraphRunResponse {
  execution_user?: string | null;
  execution_role?: string | null;
  id: string;
  graph_id: string;
  trigger_type: TriggerType;
  state: GraphRunState;
  overlap_policy: OverlapPolicy;
  started_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
}

export interface GraphRunListResponse {
  runs: GraphRunResponse[];
  /** Total runs for the graph, independent of `limit`/`offset`. */
  count: number;
}

export interface NodeRunResponse {
  id: string;
  task_id: string | null;
  attempt: number;
  state: TaskRunState;
  delegated: boolean;
  starrocks_query_id: string | null;
  error_message: string | null;
  /** What a script or Python node reported (printed output, statement summary). */
  output?: string | null;
  started_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
}

export interface GraphRunDetailResponse {
  run: GraphRunResponse;
  node_runs: NodeRunResponse[];
}

const BASE = "/task-orchestration";

/** Resume or suspend a graph's schedule or stream trigger (ALTER TASK root RESUME|SUSPEND). */
export const setGraphLifecycle = (
  graphId: string,
  state: LifecycleState,
): Promise<GraphSummary> =>
  api.post<GraphSummary>(
    `${BASE}/graphs/${encodeURIComponent(graphId)}/lifecycle`,
    { state },
  );

export const runGraph = (graphId: string): Promise<GraphRunResponse> =>
  api.post<GraphRunResponse>(
    `${BASE}/graphs/${encodeURIComponent(graphId)}/runs`,
  );

export const fetchTaskSQL = (
  graphId: string,
  taskId: string,
  signal?: AbortSignal,
) =>
  api.get<TaskCode>(
    `${BASE}/graphs/${encodeURIComponent(graphId)}/nodes/${encodeURIComponent(taskId)}/sql`,
    signal,
  );

export const fetchGraphs = async (
  signal?: AbortSignal,
): Promise<GraphListResponse> =>
  api.get<GraphListResponse>(`${BASE}/graphs`, signal);

export const fetchGraph = async (
  graphId: string,
  signal?: AbortSignal,
): Promise<GraphDetailResponse> =>
  api.get<GraphDetailResponse>(
    `${BASE}/graphs/${encodeURIComponent(graphId)}`,
    signal,
  );

export const fetchGraphRuns = async (
  graphId: string,
  signal?: AbortSignal,
): Promise<GraphRunListResponse> =>
  api.get<GraphRunListResponse>(
    `${BASE}/graphs/${encodeURIComponent(graphId)}/runs`,
    signal,
  );

/**
 * One page of a graph's run history. Paging is server-side, so a graph with a
 * long history loads ten rows rather than all of them; the response's `count`
 * is the full history size and drives the page controls.
 */
export const fetchGraphRunsPage = async (
  graphId: string,
  { limit, offset }: { limit: number; offset: number },
  signal?: AbortSignal,
): Promise<GraphRunListResponse> => {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return api.get<GraphRunListResponse>(
    `${BASE}/graphs/${encodeURIComponent(graphId)}/runs?${params.toString()}`,
    signal,
  );
};

export const fetchGraphRun = async (
  graphRunId: string,
  signal?: AbortSignal,
): Promise<GraphRunDetailResponse> =>
  api.get<GraphRunDetailResponse>(
    `${BASE}/runs/${encodeURIComponent(graphRunId)}`,
    signal,
  );
