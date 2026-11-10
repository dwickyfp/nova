import { api } from "@/lib/api-client";
import type {
  ExecutionRecord,
  LatestResponse,
  NotebookEdit,
  NotebookEditResponse,
  NotebookResponse,
  PackagesResponse,
  RunMode,
  RuntimeInfo,
  RuntimeLimits,
} from "./types";
import type { WorkspaceEntry } from "../types";

const base = (id: string) => `/notebooks/${encodeURIComponent(id)}`;

export const notebookApi = {
  create: (name: string, parentPath = "", content?: string) =>
    api.post<NotebookResponse>("/notebooks", {
      name,
      parent_path: parentPath,
      ...(content !== undefined ? { content } : {}),
    }),
  get: (id: string) => api.get<NotebookResponse>(base(id)),
  edit: (id: string, baseRevision: number, edits: NotebookEdit[]) =>
    api.patch<NotebookEditResponse>(base(id), {
      base_revision: baseRevision,
      edits,
    }),
  runtime: (id: string) =>
    api.get<{ runtime: RuntimeInfo; limits: RuntimeLimits }>(
      `${base(id)}/runtime`,
    ),
  start: (id: string) =>
    api.post<{ runtime: RuntimeInfo }>(`${base(id)}/runtime`),
  restart: (id: string) =>
    api.post<{ runtime: RuntimeInfo }>(`${base(id)}/runtime/restart`),
  stop: (id: string) =>
    api.post<{ runtime: RuntimeInfo }>(`${base(id)}/runtime/stop`),
  metrics: (id: string) =>
    api.get<{
      metrics: {
        cpu_count: number;
        cpu_percent: number;
        memory_used_mib: number;
        memory_total_mib: number;
      } | null;
    }>(`${base(id)}/runtime/metrics`),
  run: (
    id: string,
    body: {
      mode: RunMode;
      cell_id?: string;
      base_revision: number;
      on_error?: "stop" | "continue";
    },
  ) =>
    api.post<{
      run_id: string;
      executions: Array<{ execution_id: string; cell_id: string; source_revision: string }>;
    }>(`${base(id)}/executions`, body),
  cancel: (id: string, executionId?: string) =>
    api.post<{ cancelled: string[] }>(`${base(id)}/executions/cancel`, {
      execution_id: executionId ?? null,
    }),
  latest: (id: string) =>
    api.get<LatestResponse>(`${base(id)}/executions/latest`),
  execution: (id: string, executionId: string) =>
    api.get<ExecutionRecord>(`${base(id)}/executions/${encodeURIComponent(executionId)}`),
  packages: (id: string) => api.get<PackagesResponse>(`${base(id)}/packages`),
  install: (id: string, name: string, version?: string) =>
    api.post<{ operation_id: string }>(`${base(id)}/packages`, {
      name,
      version: version || null,
    }),
  removePackage: (id: string, name: string) =>
    api.delete<{ restart_required: boolean }>(
      `${base(id)}/packages/${encodeURIComponent(name)}`,
    ),
  upload: (id: string, file: File, folder = "data") => {
    const form = new FormData();
    form.append("file", file);
    form.append("folder", folder);
    return api.upload<{
      entry: WorkspaceEntry;
      runtime_path: string;
      relative_path: string;
    }>(`${base(id)}/files`, form);
  },
  exportUrl: (id: string) => `${base(id)}/export`,
};
