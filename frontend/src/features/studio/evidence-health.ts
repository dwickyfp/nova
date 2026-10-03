import { z } from "zod";

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
};

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
