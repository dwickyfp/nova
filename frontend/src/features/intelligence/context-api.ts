import { api } from "@/lib/api-client";
import type { SemanticRef } from "./lifecycle-api";

export type ContextNode = {
  id: string;
  name: string;
  kind: string;
  reference_id: string;
  state: string;
  authority?: string;
  semantic?: SemanticRef;
  source_kind?: string;
  authority_basis?: Record<string, string | number | boolean>;
  validity?: string;
  freshness?: string;
  valid_from?: string | null;
  valid_until?: string | null;
  usage_count?: number;
  aliases?: string[];
  contradictions?: string[];
};

export type ContextGraph = {
  nodes: ContextNode[];
  edges: { id: string; source: string; target: string; relationship: string }[];
  bounded: boolean;
  conflicts?: { kind: string; term: string; node_ids: string[]; resolved: boolean }[];
};

export type ContextResolution = {
  status: "resolved" | "ambiguous" | "unknown";
  selected_metric: string | null;
  node_id: string | null;
  selected_node: ContextNode | null;
  semantic: SemanticRef;
  graph: ContextGraph | null;
};

export function resolveContextMetric(semantic: SemanticRef, metric: string) {
  return api.post<ContextResolution>("/intelligence/context/resolve-metric", {
    semantic,
    term: metric,
    exact: true,
    include_context: true,
  });
}

export type CanonicalContextSource = {
  kind: "mission" | "action" | "deliverable" | "agent_release" | "dashboard" | "artifact" | "document";
  id: string;
  revision?: number;
  parent_id?: string;
  agent_id?: string;
  run_id?: string;
  fingerprint?: string;
};

export function projectCanonicalContext(source: CanonicalContextSource) {
  return api.post<{ root_id: string; nodes: number; edges: number; bounded: true }>(
    "/intelligence/context/project-canonical", source,
  );
}
