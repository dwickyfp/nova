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
  const mission = rows.find((item) => item.mission_id === selectedId) ?? rows[0];
  const latestQuestion = [...turns].reverse().find((turn) => turn.question)?.question ?? "";
  const create = useMutation({
    mutationFn: () => {
      const signature = `${threadId}:${latestQuestion}`;
      if (operation.current?.signature !== signature) operation.current = { signature, id: crypto.randomUUID() };
      return workflowApi.create(threadId, { objective: latestQuestion, work_intent: "INVESTIGATE", operation_id: operation.current.id, new_mission: true });
    },
    onSuccess: (value) => {
      setSelectedId(value.mission_id);
      void client.invalidateQueries({ queryKey });
    },
  });
  if (unavailable) return null;
  if (missions.isPending || (missions.isSuccess && !available)) return null;
  const evidence = turns.flatMap((turn) => turn.evidence ?? []);
  const previews = Object.fromEntries(turns.flatMap((turn) => turn.steps.filter((step) => step.preview).map((step) => [step.id, step.preview!]))) as Record<string, string>;
  const refresh = () => { void client.invalidateQueries({ queryKey }); };
  const content = <Tabs value={tab} onValueChange={setTab} className="min-h-0 min-w-0 flex-1 gap-0">
    <div className="shrink-0 border-b border-border p-3">
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
          {rows.length > 1 && <label className="block space-y-2 text-xs font-medium">Mission
            <select className="min-h-11 w-full min-w-0 rounded-md border bg-card px-2 text-sm" value={mission?.mission_id} onChange={(event) => setSelectedId(event.target.value)}>
              {rows.map((item) => <option key={item.mission_id} value={item.mission_id}>{item.objective}</option>)}
            </select>
          </label>}
          {mission ? <MissionPanel key={mission.mission_id} mission={mission} onRefresh={refresh} /> : <EmptyState
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
      <TabsContent value="evidence"><EvidencePanel evidence={evidence} sqlPreviews={previews} /></TabsContent>
      <TabsContent value="context"><ContextPanel /></TabsContent>
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
