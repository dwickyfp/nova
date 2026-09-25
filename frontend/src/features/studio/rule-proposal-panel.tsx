import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { agentsApi, type AgentMemory, type RuleProposalPreview } from "@/features/agents/api";
import { semanticViewsApi, type SemanticExpression } from "@/features/intelligence/semantic-views-api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

type MetricChoice = { name: string; expression?: SemanticExpression };

function expressionText(value: SemanticExpression | undefined): string {
  if (typeof value === "string") return value;
  return value?.dialects?.find((item) => item.dialect === "ANSI_SQL")?.expression ?? "";
}

export function RuleProposalPanel({ agentId, memory }: {
  agentId: string;
  memory: AgentMemory;
}) {
  const queryClient = useQueryClient();
  const [modelId, setModelId] = useState("");
  const [metricName, setMetricName] = useState("");
  const [expression, setExpression] = useState("");
  const [preview, setPreview] = useState<RuleProposalPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const agent = useQuery({
    queryKey: ["agents", agentId, "detail"],
    queryFn: () => agentsApi.get(agentId),
  });
  const modelIds = agent.data?.semantic_view_ids ?? [];
  const activeModelId = modelId || modelIds[0] || "";
  const model = useQuery({
    queryKey: ["intelligence", "semantic", activeModelId],
    queryFn: () => semanticViewsApi.get(activeModelId),
    enabled: Boolean(activeModelId),
  });
  const proposals = useQuery({
    queryKey: ["agents", "rule-proposals", activeModelId],
    queryFn: () => agentsApi.listRuleProposals(activeModelId),
    enabled: Boolean(activeModelId),
  });
  const activeDefinition = model.data?.versions.find(
    (item) => item.version === model.data?.active_version,
  )?.definition;
  const metrics = Array.isArray(activeDefinition?.metrics)
    ? (activeDefinition.metrics as MetricChoice[]).filter(
      (item) => typeof item?.name === "string" && Boolean(expressionText(item.expression)),
    ) : [];
  const activeMetric = metricName || metrics[0]?.name || "";
  const existing = proposals.data?.proposals.find(
    (item) => item.memory_id === memory.memory_id && item.status === "pending",
  );

  useEffect(() => {
    setExpression(expressionText(metrics.find((item) => item.name === activeMetric)?.expression));
    setPreview(null);
  }, [activeMetric, model.data?.id]);

  async function createProposal() {
    if (!activeModelId || !activeMetric || !expression.trim()) return;
    setBusy(true);
    try {
      await agentsApi.createRuleProposal(activeModelId, {
        agent_id: agentId,
        memory_id: memory.memory_id,
        metric_name: activeMetric,
        proposed_expression: expression.trim(),
      });
      await queryClient.invalidateQueries({ queryKey: ["agents", "rule-proposals", activeModelId] });
      toast.success("Rule proposal saved. Preview its impact before approval.");
    } catch {
      toast.error("Rule proposal could not be saved. Check the metric expression.");
    } finally {
      setBusy(false);
    }
  }

  async function previewProposal() {
    if (!existing) return;
    setBusy(true);
    try {
      setPreview(await agentsApi.previewRuleProposal(activeModelId, existing.proposal_id));
      await queryClient.invalidateQueries({ queryKey: ["agents", "rule-proposals", activeModelId] });
    } catch {
      toast.error("Preview failed. The rule or source data may have changed.");
    } finally {
      setBusy(false);
    }
  }

  async function review(approve: boolean) {
    if (!existing) return;
    setBusy(true);
    try {
      if (approve) {
        await agentsApi.approveRuleProposal(activeModelId, existing.proposal_id);
      } else {
        await agentsApi.rejectRuleProposal(activeModelId, existing.proposal_id);
      }
      setPreview(null);
      await queryClient.invalidateQueries({ queryKey: ["agents", "rule-proposals", activeModelId] });
      await queryClient.invalidateQueries({ queryKey: ["intelligence", "semantic", activeModelId] });
      toast.success(approve ? "Business rule approved" : "Rule proposal rejected");
    } catch {
      toast.error("Review failed. Refresh the model and preview again.");
    } finally {
      setBusy(false);
    }
  }

  if (agent.isLoading || model.isLoading || proposals.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading Semantic View…</p>;
  }
  if (agent.isError || model.isError || proposals.isError) {
    return <p role="alert" className="text-sm text-destructive">Business rules could not be loaded.</p>;
  }
  if (!modelIds.length) {
    return <p className="text-sm text-muted-foreground">Bind a published Semantic View to this agent before proposing a business rule.</p>;
  }

  return (
    <section className="min-w-0 space-y-4 rounded-md border p-3" aria-label="Business rule proposal">
      <div>
        <h3 className="text-sm font-medium">Propose a business rule</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          This memory is a statement from your conversation. A metric changes only after you preview and approve its expression.
        </p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="rule-model">Semantic View</Label>
        <Select value={activeModelId} onValueChange={(value) => { setModelId(value); setMetricName(""); setPreview(null); }}>
          <SelectTrigger id="rule-model" className="w-full"><SelectValue placeholder="Choose a View" /></SelectTrigger>
          <SelectContent>{modelIds.map((id) => <SelectItem key={id} value={id}>{id === activeModelId ? model.data?.name || id : id}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="rule-metric">Metric</Label>
        <Select value={activeMetric} onValueChange={(value) => { setMetricName(value); setPreview(null); }}>
          <SelectTrigger id="rule-metric" className="w-full"><SelectValue placeholder="Choose a metric" /></SelectTrigger>
          <SelectContent>{metrics.map((item) => <SelectItem key={item.name} value={item.name}>{item.name}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      {existing ? (
        <div className="space-y-3 text-sm">
          <p>Pending change to <strong>{existing.metric_name}</strong></p>
          <div className="grid min-w-0 gap-2 sm:grid-cols-2">
            <div className="min-w-0 rounded-md bg-muted p-2"><p className="text-xs text-muted-foreground">Current expression</p><code className="break-all text-xs">{existing.prior_expression}</code></div>
            <div className="min-w-0 rounded-md bg-muted p-2"><p className="text-xs text-muted-foreground">Proposed expression</p><code className="break-all text-xs">{existing.proposed_expression}</code></div>
          </div>
          {preview ? (
            <div className="rounded-md border p-3" aria-live="polite">
              <p className="font-medium">Preview result</p>
              <p>Current: {preview.prior_value ?? "No value"}</p>
              <p>Proposed: {preview.proposed_value ?? "No value"}</p>
              <details className="mt-2 min-w-0"><summary className="cursor-pointer text-xs">View generated queries</summary>
                <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-all text-xs">{preview.prior_sql}{"\n\n"}{preview.proposed_sql}</pre>
              </details>
            </div>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void previewProposal()}>Preview impact</Button>
            <Button size="sm" disabled={busy || !preview} onClick={() => void review(true)}>Approve rule</Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => void review(false)}>Reject</Button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <Label htmlFor="rule-expression">Proposed metric expression</Label>
          <Textarea id="rule-expression" value={expression} onChange={(event) => setExpression(event.target.value)} className="min-h-24 font-mono text-xs" maxLength={4000} />
          <Button size="sm" disabled={busy || !activeMetric || !expression.trim()} onClick={() => void createProposal()}>Save proposal</Button>
        </div>
      )}
    </section>
  );
}
