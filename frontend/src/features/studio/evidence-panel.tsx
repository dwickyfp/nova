import { StatusBadge } from "@/components/ui/status-badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import type { SemanticRef } from "@/features/intelligence/lifecycle-api";
import { evidenceIdentity, type ToolEvidence } from "./evidence-health";

const readable = (value: string) => value.split("_").join(" ");
const causal: Record<ToolEvidence["health"]["facts"]["causal_strength"], string> = {
  arithmetic: "Arithmetic contribution",
  association: "Association",
  supported_effect: "Supported effect",
  unknown: "Causal strength unknown",
};

export function EvidencePanel({ evidence, sqlPreviews = {}, onSelectContext, missionNames = {}, scope = "conversation" }: {
  evidence: ToolEvidence[];
  sqlPreviews?: Record<string, string>;
  onSelectContext?: (semantic: SemanticRef, metric: string) => void;
  missionNames?: Record<string, string>;
  scope?: "mission" | "conversation";
}) {
  if (!evidence.length) return <EmptyState
    title={scope === "mission" ? "No evidence recorded for this mission" : "Evidence has not been assessed"}
    description={scope === "mission" ? "Run a governed query in this mission, or select All conversation evidence to inspect other turns." : "Run a governed investigation or query. Older answers may not include an evidence assessment."}
  />;
  return <section aria-label="Evidence assessments" className="min-w-0 space-y-4">
    <p className="text-xs text-muted-foreground">Evidence health reflects recorded execution and sources. Causal strength is assessed separately.</p>
    {evidence.map((item) => {
      const { toolCallId, toolName, health, envelope, workflowProvenance } = item;
      const preview = item.sqlPreview ?? sqlPreviews[evidenceIdentity(item)] ?? (!item.runId && !workflowProvenance?.run_id ? sqlPreviews[toolCallId] : undefined);
      return <article key={evidenceIdentity(item)} className="min-w-0 space-y-3 rounded-md border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="break-words text-sm font-medium">{readable(toolName)}</h3>
        <StatusBadge tone={health.label === "strong" ? "success" : health.label === "insufficient" ? "danger" : health.label === "limited" ? "warning" : "neutral"}>
          {health.label[0].toUpperCase() + health.label.slice(1)} evidence
        </StatusBadge>
      </div>
      <p className="break-words text-xs text-muted-foreground">{workflowProvenance?.mission_id ? `Mission: ${missionNames[workflowProvenance.mission_id] ?? "Recorded mission"}` : workflowProvenance ? "Conversation evidence" : "Conversation evidence · Mission provenance was not recorded"}</p>
      <dl className="grid min-w-0 grid-cols-2 gap-x-3 gap-y-2 text-xs">
        {[
          ["Definition", readable(health.facts.semantic_grounding)],
          ["Execution", readable(health.facts.execution_status)],
          ["Coverage", readable(health.facts.coverage)],
          ["Freshness", readable(health.data_freshness.status)],
          ["Verified query", health.facts.verified_query_hit === null ? "Unknown" : health.facts.verified_query_hit ? "Matched" : "Not matched"],
          ["Causal strength", causal[health.facts.causal_strength]],
        ].map(([label, value]) => <div key={label} className="min-w-0">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="mt-1 break-words">{value}</dd>
        </div>)}
      </dl>
      {health.facts.semantic_version != null && <p className="text-xs text-muted-foreground">Semantic version {health.facts.semantic_version}</p>}
      {envelope && <div className="min-w-0 space-y-2 text-xs">
        <p className="break-words">Metrics: {envelope.metrics.join(", ") || "None recorded"}</p>
        {envelope.dimensions.length > 0 && <p className="break-words">Dimensions: {envelope.dimensions.join(", ")}</p>}
        {envelope.current_window && <p className="break-words">Current: {envelope.current_window.start} → {envelope.current_window.end}</p>}
        {envelope.baseline_window && <p className="break-words">Baseline: {envelope.baseline_window.start} → {envelope.baseline_window.end}</p>}
        {envelope.timezone && <p>Calendar timezone: {envelope.timezone}</p>}
        {envelope.filter_shape.length > 0 && <p className="break-words">Filters: {envelope.filter_shape.map((item) => `${item.field} ${item.operator}`).join(", ")}</p>}
        {envelope.warnings.map((warning) => <p key={warning} className="break-words">{readable(warning)}</p>)}
        {envelope.semantic && onSelectContext && envelope.metrics.map((metric) => <Button key={metric} variant="outline" className="min-h-11 max-w-full whitespace-normal" onClick={() => onSelectContext(envelope.semantic!, metric)}>View {metric} context</Button>)}
      </div>}
      {health.data_freshness.data_as_of && <p className="text-xs text-muted-foreground">Data as of {new Date(health.data_freshness.data_as_of).toLocaleString()}</p>}
      {health.reasons.length > 0 && <ul aria-label="Evidence limitations" className="list-inside list-disc space-y-1 text-xs">
        {health.reasons.map((reason) => <li key={reason} className="break-words">{readable(reason)}</li>)}
      </ul>}
      {health.unknown_signals.length > 0 && <p className="break-words text-xs text-muted-foreground">Unknown: {health.unknown_signals.map(readable).join(", ")}</p>}
      {preview && <details className="min-w-0 rounded border p-2">
        <summary className="min-h-11 cursor-pointer content-center text-xs font-medium focus-visible:outline focus-visible:outline-ring">SQL details</summary>
        <pre className="mt-2 max-w-full overflow-x-auto text-xs">{preview}</pre>
      </details>}
      <p className="text-xs text-muted-foreground">Assessed {new Date(health.assessed_at).toLocaleString()} · {health.rule_version}</p>
    </article>; })}
  </section>;
}
