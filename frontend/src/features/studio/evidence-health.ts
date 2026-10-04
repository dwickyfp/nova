import { z } from "zod";
import { readWorkflowProvenance } from "@/features/assistant/events";
import type { WorkflowProvenance } from "@/features/assistant/types";

const reference = z.string().max(128).nullable();
const timestamp = z.string().datetime({ offset: true });
const facts = z.object({
  semantic_grounding: z.enum(["published", "draft", "none", "unknown"]),
  semantic_view_id: reference,
  semantic_version: z.number().int().positive().nullable(),
  semantic_fingerprint: reference,
  plan_source: z.enum(["verified_query", "turn_planner", "model_planner", "compiled", "unknown"]),
  verified_query_hit: z.boolean().nullable(),
  verified_query_id: reference,
  execution_status: z.enum(["success", "partial", "failed", "not_run", "unknown"]),
  coverage: z.enum(["complete", "truncated", "partial", "unknown"]),
  semantic_ambiguity: z.enum(["none", "resolved", "unresolved", "unknown"]),
  source_agreement: z.enum(["consistent", "conflicting", "unknown"]),
  causal_strength: z.enum(["arithmetic", "association", "supported_effect", "unknown"]),
  unsupported_numeric_claims: z.boolean().nullable(),
  data_as_of: timestamp.nullable(),
  max_age_seconds: z.number().int().nonnegative().nullable(),
  evidence_refs: z.array(z.string().max(128)).max(100),
});

const health = z.object({
  schema_version: z.literal(1),
  rule_version: z.literal("evidence-health-v1"),
  assessed_at: timestamp,
  label: z.enum(["strong", "moderate", "limited", "insufficient"]),
  facts,
  data_freshness: z.object({
    status: z.enum(["fresh", "stale", "unknown"]),
    data_as_of: timestamp.nullable(),
    age_seconds: z.number().nonnegative().nullable(),
    max_age_seconds: z.number().int().nonnegative().nullable(),
  }),
  reasons: z.array(z.string().max(128)),
  unknown_signals: z.array(z.string().max(128)),
});

export type EvidenceHealth = z.infer<typeof health>;
export type ToolEvidence = {
  toolCallId: string;
  toolName: string;
  health: EvidenceHealth;
  envelope?: EvidenceEnvelope;
  workflowProvenance?: WorkflowProvenance;
  runId?: string;
  turnId?: string;
  sqlPreview?: string;
};

export function evidenceIdentity(item: Pick<ToolEvidence, "toolCallId" | "runId" | "turnId" | "workflowProvenance">): string {
  return JSON.stringify([item.workflowProvenance?.run_id ?? item.runId ?? item.turnId ?? null, item.toolCallId]);
}

export function mergeToolEvidence(items: ToolEvidence[], incoming: ToolEvidence): ToolEvidence[] {
  const key = evidenceIdentity(incoming);
  const index = items.findIndex((item) => evidenceIdentity(item) === key);
  if (index < 0) return [...items, incoming];
  const previous = items[index];
  const merged = { ...previous, ...incoming,
    envelope: incoming.envelope ?? previous.envelope,
    workflowProvenance: incoming.workflowProvenance ?? previous.workflowProvenance,
    sqlPreview: incoming.sqlPreview ?? previous.sqlPreview,
  };
  if (merged.envelope) merged.envelope = { ...merged.envelope, health: merged.health };
  return items.map((item, position) => position === index ? merged : item);
}

export function workflowProvenanceFromTrace(step: unknown): WorkflowProvenance | null {
  if (!step || typeof step !== "object") return null;
  const record = step as Record<string, unknown>;
  const trace = record.trace_detail && typeof record.trace_detail === "object" ? record.trace_detail as Record<string, unknown> : undefined;
  return readWorkflowProvenance(record.workflow ?? record.workflow_provenance ?? trace?.workflow ?? trace?.workflow_provenance);
}

const windowBounds = z.object({ start: timestamp, end: timestamp });
const envelope = z.object({
  schema_version: z.literal(1),
  health,
  semantic: z.object({ view_id: z.string().max(128), version: z.number().int().positive(), fingerprint: z.string().max(128) }).nullable(),
  metrics: z.array(z.string().max(128)).max(32),
  dimensions: z.array(z.string().max(128)).max(32),
  current_window: windowBounds.nullable(),
  baseline_window: windowBounds.nullable(),
  timezone: z.string().max(128).nullable(),
  filter_shape: z.array(z.object({ field: z.string().max(128), operator: z.string().max(32) })).max(32),
  named_filters: z.array(z.string().max(128)).max(32),
  warnings: z.array(z.string().max(256)).max(32),
  evidence_refs: z.array(z.string().max(128)).max(100),
  validated_plan_fingerprint: z.string().length(64),
  model_fingerprint: z.string().max(128),
});
export type EvidenceEnvelope = z.infer<typeof envelope>;

export function readEvidenceEnvelope(value: unknown): EvidenceEnvelope | null {
  const result = envelope.safeParse(value);
  return result.success ? result.data : null;
}

export function evidenceEnvelopeFromTrace(step: unknown): EvidenceEnvelope | null {
  if (!step || typeof step !== "object") return null;
  const record = step as Record<string, unknown>;
  const trace = record.trace_detail;
  return readEvidenceEnvelope(record.evidence_envelope) ??
    (trace && typeof trace === "object"
      ? readEvidenceEnvelope((trace as Record<string, unknown>).evidence_envelope) : null);
}

export function readEvidenceHealth(value: unknown): EvidenceHealth | null {
  const result = health.safeParse(value);
  return result.success ? result.data : null;
}

export function evidenceHealthFromTrace(step: unknown): EvidenceHealth | null {
  if (!step || typeof step !== "object") return null;
  const record = step as Record<string, unknown>;
  const trace = record.trace_detail;
  return readEvidenceHealth(record.evidence_health) ??
    (trace && typeof trace === "object"
      ? readEvidenceHealth((trace as Record<string, unknown>).evidence_health)
      : null);
}
