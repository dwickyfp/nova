import type { WorkspaceEntry } from "../types";

export type CellKind = "python" | "sql" | "markdown";
export type SqlEngine = "duckdb" | "nova";

export type NotebookCell = {
  id: string;
  kind: CellKind;
  source: string;
  name: string | null;
  engine: SqlEngine | null;
  /** A short heading the person gives the cell; not part of what runs. */
  title?: string | null;
  source_revision: string;
};

export type NotebookDocument = {
  revision: number;
  cells: NotebookCell[];
};

export type NotebookResponse = {
  entry: WorkspaceEntry;
  notebook: NotebookDocument;
};

export type NotebookEditResponse = NotebookResponse & {
  changed_cell_ids: string[];
};

export type NotebookEdit =
  | { op: "insert"; index: number; cell: Partial<NotebookCell> & { kind: CellKind } }
  | {
      op: "update";
      cell_id: string;
      source?: string;
      name?: string | null;
      title?: string | null;
      kind?: CellKind;
      engine?: SqlEngine;
    }
  | { op: "delete"; cell_id: string }
  | { op: "move"; cell_id: string; index: number };

export type RuntimeStatus =
  | "ABSENT"
  | "PROVISIONING"
  | "STARTING"
  | "BOOTSTRAPPING"
  | "READY"
  | "BUSY"
  | "STOPPING"
  | "STOPPED"
  | "FAILED"
  | "LOST";

export type RuntimeInfo = {
  runtime_id?: string;
  status: RuntimeStatus;
  stage?: string;
  generation?: number;
  idle_seconds?: number;
  active_role?: string;
  nova_attached?: boolean;
  nova_error?: string | null;
  error?: string | null;
  reason?: string | null;
  current_execution?: string | null;
};

export type RuntimeLimits = {
  cpu: string;
  memory: string;
  idle_timeout_seconds: number;
  cell_timeout_seconds: number;
  preview_rows: number;
};

export type TableSchemaColumn = { name: string; type: string };

export type TablePayload = {
  schema: TableSchemaColumn[];
  rows: Array<Array<string | number | boolean | null>>;
  limit?: number | null;
  total_rows: number | null;
  truncated: boolean;
  origin: "local_duckdb" | "nova" | "python_memory" | string;
  /** The Nova statement that reproduces this table, checked by Nova when it ran. */
  nova_sql?: string | null;
};

export type CellOutput =
  | { type: "stream"; name: "stdout" | "stderr"; text: string }
  | { type: "error"; ename: string; evalue: string; traceback: string[] }
  | { type: "table"; table: TablePayload; name?: string | null }
  | { type: "image"; mime: string; data: string }
  | { type: "svg"; svg: string }
  | { type: "html"; html: string; text?: string }
  | { type: "markdown"; text: string }
  | { type: "text"; text: string }
  | { type: "status"; text: string }
  | { type: "truncated"; text: string };

export type ExecutionStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "skipped"
  | "timeout"
  | "lost";

export type ExecutionRecord = {
  execution_id: string;
  cell_id: string;
  status: ExecutionStatus;
  execution_count?: number | null;
  source_revision?: string;
  generation?: number | null;
  duration_ms?: number | null;
  error_message?: string | null;
  outputs?: CellOutput[] | null;
  truncated?: boolean;
  sql_engine?: string | null;
  started_at?: number | string | null;
  runtime_id?: string | null;
};

export type VariableInfo = {
  name: string;
  type: string;
  kind?: string | null;
  shape?: string | null;
  preview?: string | null;
};

export type LatestResponse = {
  executions: ExecutionRecord[];
  inflight: Array<{
    cell_id: string;
    execution_id: string;
    status: "queued" | "running";
    execution_count?: number | null;
  }>;
  variables: VariableInfo[] | null;
  generation: number | null;
};

export type PackagesResponse = {
  requested: Array<{ name: string; specifier?: string; installed_version?: string | null }>;
  allowed: Array<{ name: string; specifier: string | null }>;
  base: Array<{ name: string; version: string }>;
};

export type RunMode = "cell" | "all" | "above" | "below";
