import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import type { Agent, AutoRun } from "@/features/agents/api";
import { AutoSubagentCard } from "./auto-subagent-card";
import { EvidencePanel } from "./evidence-panel";
import { MissionPanel } from "./mission-panel";
import { ResourcePanel } from "./resource-panel";
import { ContextPanel } from "./context-panel";
import type { TranscriptTurn } from "./studio-transcript";
import { workflowApi } from "./workflow-api";
import type { SemanticRef } from "@/features/intelligence/lifecycle-api";
import { mergeToolEvidence, type ToolEvidence } from "./evidence-health";

type ContextSelection = { semantic: SemanticRef; metric: string };
function contextOptions(evidence: ToolEvidence[]): ContextSelection[] {
  return [...evidence].reverse().flatMap((item) => item.envelope?.semantic
    ? item.envelope.metrics.map((metric) => ({ semantic: item.envelope!.semantic!, metric })) : []);
}
function sameContext(left: ContextSelection, right: ContextSelection) {
  return left.metric === right.metric && left.semantic.view_id === right.semantic.view_id &&
    left.semantic.version === right.semantic.version && left.semantic.fingerprint === right.semantic.fingerprint;
}

const NARROW = "(max-width: 1199px)";
function useNarrowWorkflow() {
  return useSyncExternalStore((notify) => {
    const query = window.matchMedia(NARROW);
    query.addEventListener("change", notify);
    return () => query.removeEventListener("change", notify);
  }, () => window.matchMedia(NARROW).matches, () => false);
}

export function WorkflowRail({ threadId, turns, streaming, agents, runs, runLoading, runError, retryRuns, onSelectChild, onAvailable, mobileOpen, onMobileOpenChange }: {
  threadId: string;
  turns: TranscriptTurn[];
  streaming: boolean;
  agents: Agent[];
  runs: AutoRun[];
  runLoading: boolean;
  runError: boolean;
  retryRuns: () => void;
  onSelectChild: (id: string) => void;
  onAvailable: (available: boolean) => void;
  mobileOpen: boolean;
  onMobileOpenChange: (open: boolean) => void;
}) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  return <ScopedRail key={`${epoch}:${threadId}`} {...{ threadId, turns, streaming, agents, runs, runLoading, runError, retryRuns, onSelectChild, onAvailable, mobileOpen, onMobileOpenChange, epoch }} />;
}

function ScopedRail({ threadId, turns, streaming, agents, runs, runLoading, runError, retryRuns, onSelectChild, onAvailable, mobileOpen, onMobileOpenChange, epoch }: Parameters<typeof WorkflowRail>[0] & { epoch: number }) {
  const narrow = useNarrowWorkflow();
  const [tab, setTab] = useState("activity");
  const [contextSelection, setContextSelection] = useState<ContextSelection>();
  const [showAgents, setShowAgents] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const operation = useRef<{ signature: string; id: string } | null>(null);
  const client = useQueryClient();
  const queryKey = ["studio", "workflow", epoch, threadId];
  const missions = useQuery({
    queryKey,
    queryFn: ({ signal }) => workflowApi.list(threadId, signal),
    retry: false,
    staleTime: 0,
    gcTime: 0,
    refetchInterval: (query) => streaming || query.state.data?.missions?.some((mission) => ["running", "cancelling"].includes(mission.status)) ? 1500 : false,
  });
  const unavailable = missions.error instanceof ApiError && [403, 404].includes(missions.error.status);
  const available = missions.isSuccess && Array.isArray(missions.data?.missions);
  useEffect(() => {
    onAvailable(available);
    return () => onAvailable(false);
  }, [available, onAvailable]);
  const rows = missions.data?.missions ?? [];
  const mission = selectedId === "conversation" ? undefined : rows.find((item) => `mission:${item.mission_id}` === selectedId) ?? rows[0];
  const latestQuestion = [...turns].reverse().find((turn) => turn.question)?.question ?? "";
  const create = useMutation({
    mutationFn: () => {
      const signature = `${threadId}:${latestQuestion}`;
      if (operation.current?.signature !== signature) operation.current = { signature, id: crypto.randomUUID() };
      return workflowApi.create(threadId, { objective: latestQuestion, work_intent: "INVESTIGATE", operation_id: operation.current.id, new_mission: true });
    },
    onSuccess: (value) => {
      setSelectedId(`mission:${value.mission_id}`);
      void client.invalidateQueries({ queryKey });
    },
  });
  if (unavailable) return null;
  if (missions.isPending || (missions.isSuccess && !available)) return null;
  const allEvidence = turns.reduce<ToolEvidence[]>((items, turn) => (turn.evidence ?? []).reduce((current, item) => mergeToolEvidence(current, {
    ...item,
    turnId: turn.id,
    sqlPreview: item.sqlPreview ?? (!item.runId && !item.workflowProvenance?.run_id ? turn.steps.find((step) => step.id === item.toolCallId)?.preview : undefined),
  }), items), []);
  const evidence = mission ? allEvidence.filter((item) => item.workflowProvenance?.mission_id === mission.mission_id) : allEvidence;
  const candidates = contextOptions(evidence);
  const selectedContext = contextSelection && candidates.find((candidate) => sameContext(candidate, contextSelection)) || candidates[0];
  const scopeKey = mission ? `mission:${mission.mission_id}` : "conversation";
  const selectScope = (value: string) => {
    const nextEvidence = value === "conversation" ? allEvidence : allEvidence.filter((item) => `mission:${item.workflowProvenance?.mission_id}` === value);
    setContextSelection((current) => current && contextOptions(nextEvidence).some((candidate) => sameContext(candidate, current)) ? current : undefined);
    setSelectedId(value);
  };
  const refresh = () => { void client.invalidateQueries({ queryKey }); };
  const content = <Tabs value={tab} onValueChange={setTab} className="min-h-0 min-w-0 flex-1 gap-0">
    <div className="shrink-0 border-b border-border p-3">
      <label className="mb-3 block space-y-2 text-xs font-medium">Evidence and context scope
        <select aria-label="Workflow mission" className="min-h-11 w-full min-w-0 rounded-md border border-input bg-card px-2 text-sm text-foreground focus-visible:outline focus-visible:outline-ring" value={scopeKey} onChange={(event) => selectScope(event.target.value)} disabled={!available}>
          {rows.map((item) => <option key={item.mission_id} value={`mission:${item.mission_id}`}>{item.objective}</option>)}
          <option value="conversation">All conversation evidence</option>
        </select>
      </label>
      <TabsList aria-label="Workflow details" className="h-11 w-full">
        <TabsTrigger value="activity" className="min-h-11">Activity</TabsTrigger>
        <TabsTrigger value="evidence" className="min-h-11">Evidence</TabsTrigger>
        <TabsTrigger value="context" className="min-h-11">Context</TabsTrigger>
      </TabsList>
    </div>
    <div className="min-h-0 min-w-0 flex-1 overflow-y-auto p-4">
      <TabsContent value="activity" className="min-w-0 space-y-5">
        {missions.isPending ? <LoadingLines rows={3} /> : missions.isError ? <EmptyState variant="error" title="Unable to load activity" description={missions.error.message}
          action={<Button variant="outline" onClick={() => void missions.refetch()}>Retry</Button>} /> : <>
          {mission ? <MissionPanel key={mission.mission_id} mission={mission} onRefresh={refresh} /> : rows.length ? <section className="space-y-2" aria-label="Conversation missions">
            <p className="text-xs text-muted-foreground">Choose a mission to inspect its recorded activity.</p>
            {rows.map((item) => <Button key={item.mission_id} variant="outline" className="min-h-11 w-full justify-start whitespace-normal text-left" onClick={() => selectScope(`mission:${item.mission_id}`)}>{item.objective}</Button>)}
          </section> : <EmptyState
            title="This conversation has no mission"
            description="Simple answers stay in chat. Start a mission to record a multi-stage investigation."
            action={latestQuestion ? <Button variant="outline" className="min-h-11" disabled={create.isPending || streaming} onClick={() => create.mutate()}>{create.isPending ? "Starting…" : "Start investigation mission"}</Button> : undefined}
          />}
          {create.isError && <p role="alert" className="text-xs text-destructive">{create.error.message}</p>}
        </>}
        {runs.length > 0 || runLoading || runError ? <div className="space-y-3">
          <Button variant="ghost" className="min-h-11 w-full justify-start" aria-expanded={showAgents} onClick={() => setShowAgents((value) => !value)}>{showAgents ? "Hide agent activity" : "View agent activity"}</Button>
          {showAgents && <AutoSubagentCard embedded {...{ runs, agents, loading: runLoading, error: runError, retry: retryRuns }} onSelectChild={(id) => { onMobileOpenChange(false); onSelectChild(id); }} />}
          {showAgents && <ResourcePanel threadId={threadId} runs={runs} />}
        </div> : null}
      </TabsContent>
      <TabsContent value="evidence"><EvidencePanel evidence={evidence} scope={mission ? "mission" : "conversation"} missionNames={Object.fromEntries(rows.map((item) => [item.mission_id, item.objective]))} onSelectContext={(semantic, metric) => { setContextSelection({ semantic, metric }); setTab("context"); }} /></TabsContent>
      <TabsContent value="context"><ContextPanel key={JSON.stringify([scopeKey, selectedContext?.semantic, selectedContext?.metric])} semantic={selectedContext?.semantic} metric={selectedContext?.metric} /></TabsContent>
    </div>
  </Tabs>;
  return narrow ? <>
    <Sheet open={mobileOpen} onOpenChange={onMobileOpenChange}><SheetContent className="w-full max-w-full gap-0 sm:max-w-md">
      <SheetHeader className="shrink-0 border-b pr-12"><SheetTitle>Workflow details</SheetTitle><SheetDescription>Recorded activity, evidence, and business context.</SheetDescription></SheetHeader>
      {content}
    </SheetContent></Sheet>
  </> : <aside aria-label="Workflow details" className="flex min-h-0 min-w-0 w-80 shrink-0 flex-col border-l border-border bg-background">
    <header className="flex min-h-14 shrink-0 items-center justify-between border-b px-4"><h2 className="text-sm font-medium">Workflow details</h2><Button variant="ghost" className="min-h-11" onClick={() => void missions.refetch()} disabled={missions.isFetching}>Refresh</Button></header>
    {content}
  </aside>;
}
