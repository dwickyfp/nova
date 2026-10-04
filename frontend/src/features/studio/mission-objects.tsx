import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { StatusBadge } from "@/components/ui/status-badge";
import { useAuthStore } from "@/stores/auth-store";
import { intelligenceApi, type BusinessAction, type Decision, type Investigation, type MonitorConfiguration, type News, type Outcome } from "@/features/intelligence/lifecycle-api";
import { DecisionCompose } from "@/features/intelligence/decision-compose";
import { ActionLifecycle } from "@/features/intelligence/action-lifecycle";
import { MonitorActionPreview } from "@/features/intelligence/action-preview";
import { DecisionDetail, EvidenceList } from "./studio-intelligence";
import { workflowApi, type Mission, type MissionObjectRef } from "./workflow-api";

const kinds = { investigation: "investigations", decision: "decisions", action: "actions", outcome: "outcomes" } as const;
const number = (value: number | undefined) => value == null ? "Unavailable" : new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);

export function ThreadMissionObjects({ threadId, onFollowUp }: { threadId: string; onFollowUp: (prompt: string) => void }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const missions = useQuery({ queryKey: ["studio", "workflow", epoch, threadId], queryFn: ({ signal }) => workflowApi.list(threadId, signal), retry: false, staleTime: 0, gcTime: 0 });
  return <>{missions.data?.missions?.filter((mission) => mission.object_refs.length).map((mission) => <div key={mission.mission_id} className="space-y-3">
    <p className="break-words text-xs font-medium text-muted-foreground">{mission.objective}</p>
    <MissionObjects mission={mission} onFollowUp={onFollowUp} />
  </div>)}</>;
}

export function MissionObjects({ mission, onFollowUp }: { mission: Mission; onFollowUp: (prompt: string) => void }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  return <section aria-label="Mission records" className="min-w-0 space-y-4">
    {mission.object_refs.map((reference) => <CanonicalCard key={`${epoch}:${reference.kind}:${reference.id}`} reference={reference} mission={mission} epoch={epoch} onFollowUp={onFollowUp} />)}
  </section>;
}

function CanonicalCard({ reference, mission, epoch, onFollowUp }: { reference: MissionObjectRef; mission: Mission; epoch: number; onFollowUp: (prompt: string) => void }) {
  const [open, setOpen] = useState(false);
  const client = useQueryClient();
  const queryKey = ["studio", "workflow", epoch, mission.thread_id, "object", reference.kind, reference.id, reference.revision];
  const detail = useQuery({ queryKey, queryFn: () => intelligenceApi.get<Investigation | Decision | Outcome | ActionSummary>(kinds[reference.kind], reference.id), retry: false, staleTime: 0, gcTime: 0 });
  const refresh = () => {
    void client.invalidateQueries({ queryKey });
    void client.invalidateQueries({ queryKey: ["studio", "workflow", epoch, mission.thread_id] });
  };
  const linkOutcome = useMutation({ mutationFn: async (outcome: Outcome) => {
    const latest = await workflowApi.get(mission.mission_id);
    return workflowApi.link(latest, { kind: "outcome", id: outcome.id, revision: outcome.revision });
  }, onSuccess: refresh });
  const record = detail.data;
  return <article className="min-w-0 rounded-lg border border-border bg-card p-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1"><h3 className="break-words text-sm font-medium">{record?.title || reference.kind[0].toUpperCase() + reference.kind.slice(1)}</h3>
        <p className="mt-1 text-xs text-muted-foreground">{reference.kind} · {record ? `Current revision ${record.revision}` : `Recorded revision ${reference.revision}`}</p>
      </div>
      {record && <StatusBadge>{record.status.split("_").join(" ")}</StatusBadge>}
    </div>
    {detail.isPending ? <LoadingLines rows={1} /> : detail.isError ? <EmptyState className="mt-3" variant="error" title="Record unavailable" description={detail.error.message} action={<Button variant="outline" onClick={() => void detail.refetch()}>Retry</Button>} /> : record && <>
      <Button variant="ghost" className="mt-2 min-h-11" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{open ? "Close details" : `Inspect ${reference.kind}`}</Button>
      {open && <div className="mt-3 min-w-0 border-t pt-4">
        {reference.kind === "investigation" && <InvestigationCard investigation={record as Investigation} mission={mission} epoch={epoch} onFollowUp={onFollowUp} />}
        {reference.kind === "decision" && <><DecisionDetail decision={record as Decision} refresh={refresh} epoch={epoch} onOutcome={(outcome) => linkOutcome.mutate(outcome)} /><DecisionAction decision={record as Decision} mission={mission} epoch={epoch} refresh={refresh} /></>}
        {reference.kind === "outcome" && <OutcomeCard outcome={record as Outcome} />}
        {reference.kind === "action" && <MissionAction actionId={reference.id} mission={mission} epoch={epoch} refresh={refresh} />}
      </div>}
      {linkOutcome.isError && <div role="alert" className="mt-3 space-y-2"><p className="text-xs text-destructive">The outcome was recorded but could not be linked to this mission: {linkOutcome.error.message}</p><Button variant="outline" className="min-h-11" onClick={() => linkOutcome.variables && linkOutcome.mutate(linkOutcome.variables)}>Retry outcome link</Button></div>}
    </>}
  </article>;
}

function InvestigationCard({ investigation, mission, epoch, onFollowUp }: { investigation: Investigation; mission: Mission; epoch: number; onFollowUp: (prompt: string) => void }) {
  const client = useQueryClient();
  const [compose, setCompose] = useState(false);
  const news = useQuery({ queryKey: ["studio", "workflow", epoch, mission.thread_id, "news", investigation.news_id], queryFn: () => intelligenceApi.get<News>("news", investigation.news_id), enabled: compose, retry: false, staleTime: 0, gcTime: 0 });
  const link = useMutation({ mutationFn: async (id: string) => {
    const decision = await intelligenceApi.get<Decision>("decisions", id);
    const latest = await workflowApi.get(mission.mission_id);
    return workflowApi.link(latest, { kind: "decision", id, revision: decision.revision });
  }, onSuccess: () => { setCompose(false); void client.invalidateQueries({ queryKey: ["studio", "workflow", epoch, mission.thread_id] }); } });
  return <section className="min-w-0 space-y-4" aria-label="Hypothesis board">
    <h4 className="text-sm font-medium">Hypotheses and contributions</h4>
    {investigation.hypotheses.length ? <ol className="space-y-3">{investigation.hypotheses.map((hypothesis) => <li key={hypothesis.id} className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap justify-between gap-2"><h5 className="break-words text-sm font-medium">{hypothesis.label}</h5><StatusBadge>{hypothesis.causal_status.split("_").join(" ")}</StatusBadge></div>
      {hypothesis.contribution != null && <p className="text-sm">Contribution: {number(hypothesis.contribution)}</p>}
      <p className="break-words text-xs text-muted-foreground">Next test: {hypothesis.next_test}</p>
    </li>)}</ol> : <p className="text-sm text-muted-foreground">The recorded evidence does not identify a driver.</p>}
    {investigation.decompositions.map((part) => <p key={part.dimension} className="text-xs text-muted-foreground">{part.dimension}: residual {part.residual} · {part.reconciled ? "Reconciled" : "Incomplete reconciliation"}. Each dimension is a separate comparison.</p>)}
    <EvidenceList evidence={investigation.evidence} />
    <div className="flex flex-wrap gap-2">
      <Button variant="outline" className="min-h-11" onClick={() => setCompose((value) => !value)} aria-expanded={compose}>{compose ? "Close scenario preparation" : "Compare scenarios and prepare decision"}</Button>
      <Button variant="ghost" className="min-h-11" onClick={() => onFollowUp(`Continue investigation ${investigation.id}. Test the recorded hypotheses using governed evidence and preserve the original comparison window.`)}>Continue investigation</Button>
    </div>
    {compose && (news.isPending ? <LoadingLines rows={2} /> : news.isError ? <EmptyState variant="error" title="Comparison unavailable" description={news.error.message} action={<Button variant="outline" onClick={() => void news.refetch()}>Retry</Button>} /> : news.data && <DecisionCompose news={news.data} investigation={investigation} onCreated={(id) => link.mutate(id)} />)}
    {link.isPending && <p role="status" className="text-xs text-muted-foreground">Linking the decision to this mission…</p>}
    {link.isError && <div role="alert" className="space-y-2"><p className="text-xs text-destructive">The decision was created but could not be linked: {link.error.message}</p><Button variant="outline" className="min-h-11" onClick={() => link.variables && link.mutate(link.variables)}>Retry decision link</Button></div>}
  </section>;
}

function OutcomeCard({ outcome }: { outcome: Outcome }) {
  return <section className="space-y-3" aria-label="Observed outcome">
    <dl className="grid gap-3 text-sm sm:grid-cols-3">{[["Observed", number(outcome.actual)], ["Predicted", number(outcome.predicted)], ["Completeness", `${Math.round(outcome.completeness * 100)}%`]].map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd>{value}</dd></div>)}</dl>
    <p className="text-sm">Attribution: {outcome.attribution.split("_").join(" ")}</p>
    <EvidenceList evidence={outcome.evidence} />
  </section>;
}

type ActionSummary = { id: string; revision: number; title?: string; status: string; };

function MissionAction({ actionId, mission, epoch, refresh }: { actionId: string; mission: Mission; epoch: number; refresh: () => void }) {
  const action = useQuery({ queryKey: ["intelligence", epoch, "action", actionId], queryFn: () => intelligenceApi.get<BusinessAction>("actions", actionId), staleTime: 0, gcTime: 0, retry: false });
  const policy = useQuery({ queryKey: ["intelligence", epoch, "policy", action.data?.decision_id], queryFn: () => intelligenceApi.policy(action.data!.decision_id), enabled: Boolean(action.data), staleTime: 0, gcTime: 0, retry: false });
  return <ActionLifecycle actionId={actionId} threadId={mission.thread_id} canReview={!policy.isFetching && !policy.isError && policy.data?.current && policy.data.can_review} onChanged={refresh} />;
}

function DecisionAction({ decision, mission, epoch, refresh }: { decision: Decision; mission: Mission; epoch: number; refresh: () => void }) {
  const [open, setOpen] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const lineage = useQuery({ queryKey: ["intelligence", epoch, "lineage", decision.id, decision.revision], queryFn: () => intelligenceApi.lineage(decision.id), enabled: open, staleTime: 0, gcTime: 0, retry: false });
  const monitorId = lineage.data?.news.monitor_id;
  const monitor = useQuery({ queryKey: ["studio", "workflow", epoch, mission.thread_id, "monitor", monitorId], queryFn: () => intelligenceApi.get<MonitorConfiguration>("monitors", monitorId!), enabled: open && Boolean(monitorId), staleTime: 0, gcTime: 0, retry: false });
  const policy = useQuery({ queryKey: ["intelligence", epoch, "policy", decision.id, decision.revision], queryFn: () => intelligenceApi.policy(decision.id), enabled: open, staleTime: 0, gcTime: 0, retry: false });
  const link = useMutation({ mutationFn: async (action: BusinessAction) => {
    const latest = await workflowApi.get(mission.mission_id);
    return workflowApi.link(latest, { kind: "action", id: action.id, revision: action.revision });
  }, onSuccess: refresh });
  if (!decision.selected_option_id || !["approved", "selected", "observing", "evaluated"].includes(decision.status)) return null;
  const source = monitor.data;
  const configuration: MonitorConfiguration | undefined = source && {
    name: source.name, agent_id: source.agent_id, semantic: source.semantic, plan: source.plan,
    value_column: source.value_column, count_column: source.count_column, time_dimension: source.time_dimension,
    completeness_column: source.completeness_column, driver_dimensions: source.driver_dimensions,
    related_monitor_ids: source.related_monitor_ids, relative_threshold: source.relative_threshold,
    absolute_threshold: source.absolute_threshold, minimum_samples: source.minimum_samples,
    baseline_weeks: source.baseline_weeks, window_hours: source.window_hours, cooldown_hours: source.cooldown_hours,
    cadence_minutes: source.cadence_minutes, timezone: source.timezone, enabled,
  };
  const error = lineage.error ?? monitor.error ?? policy.error;
  return <section className="mt-5 space-y-3 border-t pt-4" aria-label="Decision to action">
    <Button variant="outline" className="min-h-11" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{open ? "Close action preparation" : "Prepare governed monitor action"}</Button>
    {open && (error ? <EmptyState variant="error" title="Action preparation unavailable" description={error.message} action={<Button variant="outline" onClick={() => { void lineage.refetch(); void monitor.refetch(); void policy.refetch(); }}>Reload preparation</Button>} /> : !configuration || policy.isPending ? <LoadingLines rows={2} /> : <>
      <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />Enable the monitor schedule after approved execution</label>
      <MonitorActionPreview decision={decision} configuration={configuration} threadId={mission.thread_id} canReview={!policy.isFetching && policy.data?.current && policy.data.can_review} onCreated={(action) => link.mutate(action)} />
      {link.isError && <div role="alert" className="space-y-2"><p className="text-xs text-destructive">The action was created, but could not be linked to this mission: {link.error.message}</p><Button variant="outline" className="min-h-11" onClick={() => link.variables && link.mutate(link.variables)}>Retry action link</Button></div>}
    </>)}
  </section>;
}
