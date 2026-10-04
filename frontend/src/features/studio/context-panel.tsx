import { ContextInspector } from "@/features/intelligence/context-inspector";
import type { ContextGraph } from "@/features/intelligence/context-api";
import type { SemanticRef } from "@/features/intelligence/lifecycle-api";
import { StatusBadge } from "@/components/ui/status-badge";
export function ContextPanel({ semantic, metric }: { semantic?: SemanticRef; metric?: string }) {
  return <ContextInspector semantic={semantic} metric={metric} renderGraphFooter={(graph) => <ContextAuthority graph={graph} />} />;
}

function ContextAuthority({ graph }: { graph: ContextGraph }) {
  if (!graph?.nodes?.length) return null;
  const conflicts = graph.conflicts?.filter((conflict) => !conflict.resolved) ?? [];
  return <section aria-label="Context authority and conflicts" className="min-w-0 space-y-4 border-t pt-4">
    <h3 className="text-sm font-medium">Source authority and validity</h3>
    <p className="text-xs text-muted-foreground">Published and reviewed definitions take precedence over usage. Conflicting authoritative definitions require review.</p>
    {conflicts.length > 0 && <div role="status" className="space-y-2 rounded-md border border-warning bg-warning/10 p-3">
      <p className="text-sm font-medium">Unresolved definition conflicts</p>
      <ul className="space-y-1 text-xs">{conflicts.map((conflict) => <li key={`${conflict.kind}:${conflict.term}`} className="break-words">{conflict.term} · {conflict.kind.split("_").join(" ")}</li>)}</ul>
    </div>}
    <ul className="space-y-3">{graph.nodes.map((node) => <li key={node.id} className="min-w-0 space-y-2 rounded-md border bg-card p-3">
      <h4 className="break-words text-sm font-medium">{node.name}</h4>
      <div className="flex flex-wrap gap-2"><StatusBadge>{node.source_kind?.split("_").join(" ") || "Source unknown"}</StatusBadge><StatusBadge tone={node.validity === "conflicted" ? "warning" : "neutral"}>{node.validity?.split("_").join(" ") || "Validity unknown"}</StatusBadge></div>
      {node.authority_basis && Object.keys(node.authority_basis).length > 0 && <dl className="space-y-1 text-xs">{Object.entries(node.authority_basis).map(([label, value]) => <div key={label} className="break-words"><dt className="inline text-muted-foreground">{label.split("_").join(" ")}: </dt><dd className="inline">{String(value).split("_").join(" ")}</dd></div>)}</dl>}
      <p className="text-xs text-muted-foreground">Freshness: {node.freshness?.split("_").join(" ") || "unknown"}{node.usage_count != null ? ` · Recorded uses: ${node.usage_count}` : ""}</p>
      {node.valid_until && <p className="text-xs text-muted-foreground">Valid until {new Date(node.valid_until).toLocaleString()}</p>}
      {node.aliases?.length ? <p className="break-words text-xs">Aliases: {node.aliases.join(", ")}</p> : null}
      {node.contradictions?.length ? <p className="break-words text-xs">Contradictions: {node.contradictions.length} competing definitions in this graph.</p> : null}
    </li>)}</ul>
  </section>;
}
