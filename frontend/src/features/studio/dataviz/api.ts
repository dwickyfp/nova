import { api } from "@/lib/api-client";

/** Where a result sits in the caller's own conversation; the server reads the rest. */
export type DatavizLocator = {
  thread_id: string;
  message_id: string;
  content_id: string;
  node?: {
    component: "Chart" | "Table" | "Kpi" | "GeoMap";
    evidence_id: string;
    x?: string;
    y?: string;
    type?: string;
    title?: string;
    column?: string;
  };
};

export type DatavizConcept = {
  qualified_name?: string | null;
  label?: string | null;
  definition?: string | null;
  metric?: string | null;
};

export type DatavizCapability = {
  preview_ref: string | null;
  status: "SUPPORTED" | "CONVERTIBLE" | "UNSUPPORTED" | "UNKNOWN";
  reason_code: string;
  message: string;
  can_add: boolean;
  title?: string;
  render_kind?: string;
  concepts?: DatavizConcept[];
};

export type DatavizDashboard = {
  id: number;
  title: string;
  published: boolean;
  own: boolean;
  changed?: string | null;
  bi_view_path: string;
};

export type DatavizTarget =
  | { mode: "existing"; dashboard_id: number; expected_revision?: string }
  | { mode: "new"; title: string };

export type DatavizEditorState = "clean" | "dirty" | "unknown";

export type DatavizPublication = {
  operation_id: string;
  status: string;
  dashboard_id: number | null;
  chart_id: number | null;
  artifact_id: string | null;
  bi_view_path: string | null;
  reused_chart: boolean;
  concepts: { qualified_name?: string | null; label?: string | null }[];
};

const BASE = "/agents/studio/dataviz";

export function locatorKey(locator: DatavizLocator): string {
  return JSON.stringify([
    locator.thread_id,
    locator.message_id,
    locator.content_id,
    locator.node ?? null,
  ]);
}

export const datavizApi = {
  check: (items: DatavizLocator[], signal?: AbortSignal) =>
    api.post<{ results: DatavizCapability[] }>(`${BASE}/capabilities/check`, { items }, signal),
  dashboards: (search: string, page: number, signal?: AbortSignal) =>
    api.get<{ dashboards: DatavizDashboard[]; page: number; has_more: boolean }>(
      `${BASE}/dashboards?search=${encodeURIComponent(search)}&page=${page}`,
      signal,
    ),
  publish: (body: {
    preview_ref: string;
    idempotency_key: string;
    target: DatavizTarget;
    title?: string;
    editor_state?: DatavizEditorState;
    open_dashboard_id?: number | null;
    agent_id?: string;
  }) => api.post<DatavizPublication>(`${BASE}/publish`, body),
};
