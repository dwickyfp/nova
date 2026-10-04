import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { StatusBadge, type StatusTone } from "@/components/ui/status-badge";
import { Markdown } from "@/features/assistant/markdown";
import { useAuthStore } from "@/stores/auth-store";
import { workflowApi, type Mission, type MissionDeliverable } from "./workflow-api";
import { InvestigationStart } from "./investigation-start";

const statusTone = (status: string): StatusTone => status === "completed" ? "success" : status === "blocked" ? "warning" : "neutral";

export function MissionPanel({ mission, onRefresh }: { mission: Mission; onRefresh: () => void }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const pending = useRef<{ signature: string; id: string } | null>(null);
  const key = ["studio", "workflow", epoch, mission.thread_id, "deliverables", mission.mission_id];
  const deliverables = useQuery({ queryKey: key, queryFn: ({ signal }) => workflowApi.deliverables(mission.mission_id, signal), retry: false, staleTime: 0, gcTime: 0 });
  const cancel = useMutation({ mutationFn: () => workflowApi.cancel(mission), onSuccess: onRefresh });
  const generate = useMutation({
    mutationFn: (kind: MissionDeliverable["kind"]) => {
      const signature = `${mission.mission_id}:${mission.revision}:${kind}`;
      if (pending.current?.signature !== signature) pending.current = { signature, id: crypto.randomUUID() };
      return workflowApi.deliver(mission, kind, pending.current.id);
    },
    onSuccess: (value) => {
      setSelected(value.deliverable_id);
      void client.invalidateQueries({ queryKey: key });
    },
  });
  const rows = deliverables.data?.deliverables ?? [];
  const document = rows.find((item) => item.deliverable_id === selected);
  const error = cancel.error ?? generate.error;
  return <section aria-label="Mission activity" className="min-w-0 space-y-5">
    <div className="space-y-2">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="break-words text-sm font-medium">{mission.objective}</h3>
        <StatusBadge tone={statusTone(mission.status)}>{mission.status}</StatusBadge>
      </div>
      <p className="text-xs text-muted-foreground">Recorded work · revision {mission.revision}</p>
      {mission.continuation && <p className="text-xs text-muted-foreground">{mission.continuation.mode === "continue" ? "Continuing mission" : "New mission"} · {mission.continuation.reason.split("_").join(" ")}</p>}
    </div>
    {mission.stages.length ? <ol className="space-y-2" aria-label="Mission stages">
      {mission.stages.map((stage) => <li key={stage.kind} className="flex min-w-0 items-start justify-between gap-2 border-b border-border py-2">
        <span className="break-words text-sm">{stage.label}</span>
        <StatusBadge tone={statusTone(stage.status)}>{stage.status}</StatusBadge>
      </li>)}
    </ol> : <p className="text-sm text-muted-foreground">Public stages have not been recorded for this work.</p>}
    {["planned", "running", "blocked"].includes(mission.status) && <Button
      variant="outline" className="min-h-11" disabled={cancel.isPending || mission.cancel_requested}
      onClick={() => cancel.mutate()}
    >{cancel.isPending ? "Requesting cancellation…" : "Cancel mission"}</Button>}
    {mission.status === "cancelling" && <p role="status" className="text-xs text-muted-foreground">Cancellation requested. Waiting for the current execution to stop.</p>}
    {mission.investigation_requirements && <InvestigationStart mission={mission} refresh={onRefresh} />}
    <section aria-label="Mission deliverables" className="space-y-3">
      <h3 className="text-sm font-medium">Deliverables</h3>
      <div className="flex flex-wrap gap-2">
        {(["analysis_summary", "investigation_report", "scenario_comparison", "decision_memo", "action_plan", "outcome_report"] as const).filter((kind) =>
          !["investigation_report", "scenario_comparison", "outcome_report"].includes(kind) || mission.object_refs.some((ref) => ref.kind === ({ investigation_report: "investigation", scenario_comparison: "decision", outcome_report: "outcome" } as Record<string, string>)[kind])
        ).map((kind) => <Button key={kind} variant="outline" className="min-h-11 whitespace-normal" disabled={generate.isPending || !mission.evidence_refs.length}
          onClick={() => generate.mutate(kind)}>{generate.isPending && generate.variables === kind ? "Generating…" : `Generate ${kind.split("_").join(" ")}`}</Button>)}
      </div>
      {!mission.evidence_refs.length && <p className="text-xs text-muted-foreground">Record evidence before generating a deliverable.</p>}
      {error && <p role="alert" className="break-words text-xs text-destructive">{error.message}</p>}
      {deliverables.isPending ? <LoadingLines rows={2} /> : deliverables.isError ? <EmptyState variant="error" title="Unable to load deliverables" description={deliverables.error.message}
        action={<Button variant="outline" onClick={() => void deliverables.refetch()}>Retry</Button>} /> : rows.length ? <ul className="space-y-2">
        {rows.map((item) => <li key={item.deliverable_id}><Button variant="ghost" className="min-h-11 h-auto w-full justify-start whitespace-normal text-left" aria-pressed={selected === item.deliverable_id}
          onClick={() => setSelected(item.deliverable_id)}>{item.title} · revision {item.mission_revision}</Button></li>)}
      </ul> : <p className="text-xs text-muted-foreground">No deliverables have been generated for this mission.</p>}
      {document && <article aria-label={document.title} className="min-w-0 space-y-3 rounded-md border bg-card p-3">
        <Markdown>{document.markdown}</Markdown>
        <details><summary className="min-h-11 cursor-pointer content-center text-xs focus-visible:outline focus-visible:outline-ring">Source references</summary>
          <ul className="mt-2 space-y-1 break-all text-xs text-muted-foreground">
            {document.evidence_refs.map((id) => <li key={id}>Evidence: {id}</li>)}
            {document.object_refs.map((ref) => <li key={`${ref.kind}:${ref.id}`}>{ref.kind}: {ref.id} · revision {ref.revision}</li>)}
            {document.sources?.map((source) => <li key={`${source.kind}:${source.id}:${source.revision}`}>{source.kind}: {source.id} · revision {source.revision} · fingerprint {source.fingerprint}{source.semantic ? ` · Semantic View ${source.semantic.view_id} v${source.semantic.version}` : ""}</li>)}
          </ul>
        </details>
      </article>}
    </section>
  </section>;
}
