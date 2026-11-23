/** Parse the artifact events of protocol v2; null for anything malformed. */
import { chartSchema, type ChartDefinition } from "@/features/native-dataviz/contracts";
import type { ArtifactDraft, ArtifactSaved, SaveCapability } from "./types";

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

export function parseDraft(value: unknown): ArtifactDraft | null {
  const data = record(value);
  if (!data || typeof data.draft_ref !== "string" || !data.draft_ref || data.kind !== "dataviz_chart") return null;
  const chart = chartSchema.safeParse(data.chart);
  const revision = data.revision;
  if (!chart.success || typeof revision !== "number" || !Number.isSafeInteger(revision) || revision < 1) return null;
  return {
    draft_ref: data.draft_ref,
    kind: "dataviz_chart",
    title: typeof data.title === "string" && data.title ? data.title : (chart.data as ChartDefinition).title,
    chart: chart.data as ChartDefinition,
    revision,
    ...(typeof data.content_id === "string" ? { content_id: data.content_id } : {}),
  };
}

export function parseSaved(value: unknown): ArtifactSaved | null {
  const data = record(value);
  if (!data) return null;
  const text = (key: string) => (typeof data[key] === "string" ? (data[key] as string) : null);
  const dashboard = text("dashboard_id");
  const workspace = text("workspace_id");
  const chart = text("chart_id");
  if (!dashboard || !workspace || !chart) return null;
  return {
    operation_id: text("operation_id") ?? "",
    dashboard_id: dashboard,
    dashboard_title: text("dashboard_title") ?? "",
    workspace_id: workspace,
    chart_id: chart,
    widget_id: text("widget_id") ?? "",
    revision: typeof data.revision === "number" ? data.revision : null,
    draft_revision: typeof data.draft_revision === "number" ? data.draft_revision : 1,
  };
}

export function parseCapability(value: unknown): SaveCapability | null {
  return value === "can_save" || value === "role_cannot_author" ? value : null;
}
