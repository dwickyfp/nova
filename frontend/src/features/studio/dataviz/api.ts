import { api } from "@/lib/api-client";

/** Where a result sits in the caller's own conversation; the server reads the rest. */
export type DatavizLocator = {
  thread_id: string;
  message_id: string;
  content_id: string;
  node?:
    /** A native chart on an answer page, by its position among the page's charts. */
    | { component: "DatavizChart"; position: number }
    /** A view of an answer from a retired catalog. */
    | {
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

/** A native dashboard the person can edit. */
export type DatavizDashboard = {
  id: string;
  title: string;
  published: boolean;
  own: boolean;
  changed?: string | null;
  workspace_id: string;
};

export type DatavizTarget =
  | { mode: "existing"; dashboard_id: string }
  | { mode: "new"; title: string };

export type DatavizPublication = {
  operation_id: string;
  status: string;
  dashboard_id: string;
  workspace_id: string;
  chart_id: string;
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
    agent_id?: string;
  }) => api.post<DatavizPublication>(`${BASE}/publish`, body),
};
