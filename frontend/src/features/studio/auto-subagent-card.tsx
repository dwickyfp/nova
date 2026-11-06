import { useCallback, useEffect, useMemo, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, ChevronRight, CircleAlert, LoaderCircle, Workflow, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/features/assistant/markdown";
import { agentsApi, type Agent, type AutoChildTimeline, type AutoRun, type AutoRunEvent } from "@/features/agents/api";
import { cn } from "@/lib/utils";
import { latestParticipants } from "./smart-agent-tree";

type SelectedChild = { rootRunId: string; childRunId: string };
type CardProps = {
  embedded?: boolean;
  runs: AutoRun[];
  agents: Agent[];
  loading: boolean;
  error: boolean;
  retry: () => void;
  onSelectChild: (childRunId: string) => void;
};

type PanelProps = {
  selected: SelectedChild | null;
  runs: AutoRun[];
  agents: Agent[];
  onClose: () => void;
  onRefreshTree: () => void;
};

const TERMINAL = new Set(["completed", "failed", "cancelled", "interrupted"]);

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: "Queued", running: "Working", waiting_for_agent: "Waiting for agents",
    waiting_for_message: "Waiting for reply", waiting_for_auth: "Sign in needed",
    waiting_for_turn: "Next turn queued", idle: "Idle",
    completed: "Done", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted",
  };
  return labels[status] ?? status.replace(/_/g, " ");
}

function Status({ status }: { status: string }) {
  const active = ["running", "waiting_for_agent", "waiting_for_message", "waiting_for_auth"].includes(status);
  const failed = ["failed", "interrupted", "cancelled"].includes(status);
  return <span className={cn("inline-flex shrink-0 items-center gap-1.5 text-xs", failed ? "text-destructive" : active ? "text-warning-strong" : status === "completed" ? "text-success-strong" : "text-muted-foreground")}>
    <span aria-hidden="true" className={cn("size-1.5 shrink-0 rounded-full", failed ? "bg-destructive" : active ? "bg-warning" : status === "completed" ? "bg-success" : "bg-muted-foreground")} />
    {statusLabel(status)}
  </span>;
}

function activitySummary(children: AutoRun[]): string {
  const count = (statuses: string[]) => children.filter((child) => statuses.includes(child.status)).length;
  const working = count(["running"]), waiting = count(["waiting_for_agent", "waiting_for_message", "waiting_for_auth"]);
  const queued = count(["queued"]), done = count(["completed"]), stopped = count(["failed", "cancelled", "interrupted"]);
  return [working && `${working} working`, waiting && `${waiting} waiting`, queued && `${queued} queued`, done && `${done} done`, stopped && `${stopped} stopped`].filter(Boolean).join(" · ") || "None yet";
}

function AgentBranch({ parentId, runs, agents, onSelectChild, visited = new Set<string>() }: {
  parentId: string; runs: AutoRun[]; agents: Agent[]; onSelectChild: (id: string) => void; visited?: Set<string>;
}) {
  return <ul className="min-w-0" aria-label="Agent children">
    {runs.filter((run) => (run.parent_agent_session_id ?? run.parent_run_id ?? (run.depth === 1 ? parentId : null)) === parentId).map((child) => {
      const id = child.agent_session_id ?? child.run_id;
      if (visited.has(id)) return null;
      const name = child.agent_name || agents.find((agent) => agent.agent_id === child.agent_id)?.name || "Specialist";
      const descendants = runs.some((run) => (run.parent_agent_session_id ?? run.parent_run_id) === id);
      return <li key={id} className="min-w-0">
        <button type="button" onClick={() => onSelectChild(child.run_id)} aria-label={`Open ${name} conversation`} className="group flex min-h-11 w-full min-w-0 items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{name}</span>{(child.turn_number ?? 1) > 1 ? <span className="block text-xs text-muted-foreground">Turn {child.turn_number}</span> : null}</span>
          <Status status={child.status} /><ChevronRight aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
        </button>
        {descendants ? <div className="ml-2 min-w-0 border-l border-border pl-1"><AgentBranch parentId={id} runs={runs} agents={agents} onSelectChild={onSelectChild} visited={new Set([...visited, id])} /></div> : null}
      </li>;
    })}
  </ul>;
}

async function loadTimeline(rootRunId: string, childRunId: string, previous?: AutoChildTimeline): Promise<AutoChildTimeline> {
  // A child can become terminal just before its final answer reaches the event journal.
  // Re-read that small, bounded journal from the start during terminal catch-up.
  const rescan = Boolean(previous && TERMINAL.has(previous.run.status));
  const events: AutoRunEvent[] = [...(previous?.events ?? [])];
  let after = rescan ? -1 : previous?.next_cursor ?? -1;
  let latest: AutoChildTimeline | null = previous ?? null;
  for (let page = 0; page < 100; page += 1) {
    const response = await agentsApi.getAutoChildTimeline(rootRunId, childRunId, after);
    events.push(...response.events);
    latest = response;
    if (!response.has_more || response.next_cursor <= after) break;
    after = response.next_cursor;
  }
  if (!latest) throw new Error("Could not load the subagent conversation.");
  return { ...latest, events: [...new Map(events.map((event) => [event.event_id, event])).values()].sort((a, b) => a.event_id - b.event_id) };
}

function Card({ root, children, agents, loading, error, retry, onSelectChild }: {
  root: AutoRun | undefined; children: AutoRun[]; agents: Agent[];
  loading: boolean; error: boolean; retry: () => void; onSelectChild: (id: string) => void;
}) {
  return <section aria-label="Smart agent activity" className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card text-card-foreground">
    <div className="flex items-center justify-between px-4 pb-1 pt-3"><span className="font-heading text-sm tracking-tight text-muted-foreground">nova</span><Workflow aria-hidden="true" className="size-4 text-muted-foreground" strokeWidth={1.75} /></div>
    <div className="flex min-h-11 items-center gap-2 px-4 pb-3 text-sm">{root?.agent_id === "__smart__" ? <button type="button" aria-label="Open Smart conversation" onClick={() => onSelectChild(root.run_id)} className="min-h-11 min-w-0 flex-1 rounded text-left font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">Smart</button> : <span className="min-w-0 flex-1 font-medium">Smart</span>}{root ? <Status status={root.status} /> : <span className="text-xs text-muted-foreground">{error ? "Unavailable" : "Loading"}</span>}</div>
    <div className="mx-4 border-t border-border" />
    <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 pt-3">
      <div className="flex items-center justify-between gap-2 px-2 pb-1"><h2 className="text-sm font-medium">Subagents</h2><span aria-live="polite" className="min-w-0 truncate text-right text-xs text-muted-foreground">{activitySummary(children)}</span></div>
      {error ? <div role="alert" className="px-2 pb-2 text-xs text-muted-foreground">Activity unavailable. <Button variant="link" size="sm" className="h-auto px-0 text-xs" onClick={retry}>Retry</Button></div> : null}
      {!root && loading ? <p className="px-2 pb-2 text-xs text-muted-foreground">Loading activity…</p> : null}
      {root && children.length === 0 ? <p className="px-2 pb-2 text-xs text-muted-foreground">{TERMINAL.has(root.status) ? "No specialist was called." : "No specialist assigned yet."}</p> : null}
      {root ? <AgentBranch parentId={root.agent_session_id ?? root.run_id} runs={children} agents={agents} onSelectChild={onSelectChild} /> : null}
    </div>
  </section>;
}

/** What a tool does, in the words of the person reading the trail. */
const TOOL_LABELS: Record<string, string> = {
  semantic_query: "Queried business data",
  semantic_view_query: "Queried business data",
  query_execute: "Ran a query",
  ml_execute: "Ran a model",
  data_to_chart: "Built a chart",
  diagnose_change: "Diagnosed a change",
  compute_metrics: "Computed figures",
  discover_agents: "Looked for specialists",
  spawn_agent: "Assigned a specialist",
  wait_agent: "Waited for specialists",
  list_agents: "Checked on specialists",
  send_message: "Messaged a teammate",
  followup_task: "Gave a follow-up task",
  dashboard_build: "Built a dashboard",
  load_skill: "Loaded a playbook",
};

type Step =
  | { kind: "tool"; id: string; tool: string; status: "running" | "done" | "failed"; request: string; sql: string; progress: string; result: string }
  | { kind: "message"; id: string; from: string; content: string; outgoing: boolean }
  | { kind: "answer"; id: string; text: string }
  | { kind: "plan"; id: string; steps: string[] }
  | { kind: "note"; id: string; text: string; tone: "muted" | "error" };

/** The question a query was asked, without the coordination context Nova appends. */
function requestText(preview: string, tool: string): string {
  const text = preview.startsWith(`${tool}:`) ? preview.slice(tool.length + 1) : preview;
  return text.split(/\n\s*\nBackground only|\n\s*\nUser asks/)[0].trim();
}

function stepsFrom(events: AutoRunEvent[], rootRunId: string, childName: string, terminal: boolean, failed: boolean): Step[] {
  const steps: Step[] = [];
  const tools = new Map<string, Extract<Step, { kind: "tool" }>>();
  const hasAnswer = events.some((event) => event.type === "child_activity" && event.payload.event_type === "answer");
  for (const event of events) {
    const payload = event.payload;
    const id = String(event.event_id);
    if (event.type === "child_activity") {
      const kind = String(payload.event_type ?? "");
      const text = typeof payload.text === "string" ? payload.text : "";
      const callId = String(payload.tool_call_id ?? "");
      if (kind === "tool_call") {
        const tool = String(payload.tool_name || "tool");
        const step = { kind: "tool" as const, id, tool, status: "running" as const, request: requestText(String(payload.sql_preview ?? ""), tool), sql: "", progress: "", result: "" };
        tools.set(callId || id, step);
        steps.push(step);
      } else if (["tool_status", "tool_progress", "tool_detail"].includes(kind)) {
        const step = tools.get(callId);
        if (!step) continue;
        if (kind === "tool_status" && (payload.status === "done" || payload.status === "failed")) step.status = payload.status;
        const preview = String(payload.sql_preview ?? "");
        if (kind === "tool_progress" && /^\s*(WITH|SELECT)\b/i.test(preview)) step.sql = preview;
        if (kind === "tool_progress" && text) step.progress = text;
        if (kind === "tool_detail" && text) step.result = text;
      } else if (kind === "answer" && text) steps.push({ kind: "answer", id, text });
      else if (kind === "plan" && Array.isArray(payload.steps) && payload.steps.length) {
        steps.push({ kind: "plan", id, steps: payload.steps.map((step) => (typeof step === "object" && step ? String((step as Record<string, unknown>).text ?? "") : "")).filter(Boolean) });
      } else if (kind === "error") steps.push({ kind: "note", id, tone: "error", text: String(payload.message ?? "The specialist stopped with an error.") });
      else if (kind === "omitted" && payload.reason === "activity_limit") steps.push({ kind: "note", id, tone: "muted", text: "Later activity was not recorded: this run reached its event limit." });
    } else if (event.type === "agent_message") {
      const sender = String(payload.sender_run_id ?? ""), recipient = String(payload.recipient_run_id ?? "");
      if (sender !== event.run_id && recipient !== event.run_id) continue;
      const content = String(payload.content ?? "");
      // The final report repeats the answer shown below it.
      if (payload.message_type === "final" || !content) continue;
      const from = payload.origin === "user" ? "You" : sender === rootRunId ? "Smart" : sender === event.run_id ? childName : String(payload.sender_agent_path || "A teammate");
      steps.push({ kind: "message", id, from, content, outgoing: sender === event.run_id });
    } else if (event.type === "agent_completed" && !hasAnswer && String(payload.summary ?? "")) {
      steps.push({ kind: "answer", id, text: String(payload.summary) });
    } else if (["agent_failed", "agent_cancelled", "agent_interrupted"].includes(event.type)) {
      steps.push({ kind: "note", id, tone: "error", text: `${childName} ${event.type.slice(6).replace(/_/g, " ")}` });
    } else if (event.type === "agent_waiting") steps.push({ kind: "note", id, tone: "muted", text: "Waiting for a message" });
  }
  // A finished run has no step still in progress: what never reported back stopped with it.
  if (terminal) for (const step of tools.values()) if (step.status === "running") step.status = failed ? "failed" : "done";
  return steps;
}

function StepMark({ status }: { status: "running" | "done" | "failed" | "note" | "error" }) {
  const base = "relative z-10 flex size-5 shrink-0 items-center justify-center rounded-full bg-background";
  if (status === "running") return <span className={base}><LoaderCircle aria-label="In progress" className="size-3.5 animate-spin text-warning-strong motion-reduce:animate-none" /></span>;
  if (status === "failed" || status === "error") return <span className={base}><CircleAlert aria-label="Failed" className="size-3.5 text-destructive" /></span>;
  if (status === "done") return <span className={base}><Check aria-label="Done" className="size-3.5 text-muted-foreground" /></span>;
  return <span className={base}><span aria-hidden="true" className="size-1.5 rounded-full bg-muted-foreground/60" /></span>;
}

function ToolStep({ step }: { step: Extract<Step, { kind: "tool" }> }) {
  const label = TOOL_LABELS[step.tool] ?? step.tool.replace(/_/g, " ");
  const line = step.status === "running" ? step.progress : step.result || step.progress;
  const details = step.request || step.sql;
  return <div className="min-w-0 flex-1 pb-4">
    <p className="flex min-w-0 items-baseline gap-2 text-sm">
      <span className="min-w-0 font-medium text-foreground">{label}</span>
      <span className="shrink-0 font-mono text-xs text-muted-foreground">{step.tool}</span>
    </p>
    {line ? <p className="mt-0.5 break-words text-sm text-muted-foreground">{line}</p> : null}
    {details ? <details className="group mt-1.5">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
        <ChevronRight aria-hidden="true" className="size-3 transition-transform group-open:rotate-90 motion-reduce:transition-none" />
        {step.sql ? "Request and query" : "Request"}
      </summary>
      <div className="mt-2 space-y-2 border-l border-border pl-3">
        {step.request ? <p className="break-words whitespace-pre-wrap text-sm text-foreground/90">{step.request}</p> : null}
        {step.sql ? <pre className="max-h-56 overflow-auto rounded-md bg-surface-1 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words"><code>{step.sql}</code></pre> : null}
      </div>
    </details> : null}
  </div>;
}

function Timeline({ steps }: { steps: Step[] }) {
  return <ol aria-label="Specialist steps" className="relative">
    {steps.map((step, index) => {
      const last = index === steps.length - 1;
      if (step.kind === "answer") return <li key={step.id} className="pt-1 pb-4">
        <article className="min-w-0 rounded-xl border border-border bg-card px-4 py-3">
          <p className="mb-2 text-xs font-medium text-muted-foreground">Answer</p>
          <Markdown className="text-sm leading-relaxed">{step.text}</Markdown>
        </article>
      </li>;
      const mark = step.kind === "tool" ? step.status : step.kind === "note" && step.tone === "error" ? "error" : "note";
      return <li key={step.id} className="relative flex min-w-0 gap-3">
        {!last ? <span aria-hidden="true" className="absolute top-5 bottom-0 left-2.5 w-px -translate-x-1/2 bg-border" /> : null}
        <StepMark status={mark} />
        {step.kind === "tool" ? <ToolStep step={step} />
          : step.kind === "message" ? <div className="min-w-0 flex-1 pb-4"><p className="text-xs text-muted-foreground">{step.from}</p><p className="mt-0.5 break-words whitespace-pre-wrap text-sm">{step.content}</p></div>
          : step.kind === "plan" ? <div className="min-w-0 flex-1 pb-4 text-sm"><p className="text-xs text-muted-foreground">Plan</p><ol className="mt-1 list-inside list-decimal space-y-0.5 text-foreground/90">{step.steps.map((item, position) => <li key={position} className="break-words">{item}</li>)}</ol></div>
          : <p role={step.tone === "error" ? "alert" : undefined} className={cn("min-w-0 flex-1 pb-4 pt-0.5 text-sm", step.tone === "error" ? "text-destructive" : "text-muted-foreground")}>{step.text}</p>}
      </li>;
    })}
  </ol>;
}

export function AutoSubagentPanel({ selected, runs, agents, onClose }: PanelProps) {
  const queryClient = useQueryClient();
  const panelRef = useRef<HTMLElement>(null);
  const closeAnimationRef = useRef<Animation | null>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const terminalCatchUpRef = useRef({ key: "", updatedAt: 0, startedAt: 0, attempts: 0, observed: false });
  const closePanel = useCallback(() => {
    if (closeAnimationRef.current) return;
    const panel = panelRef.current;
    if (!panel || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      onClose();
      return;
    }
    const animation = panel.animate(
      [{ opacity: 0, transform: "translateX(12px)" }],
      { duration: 140, easing: "ease-in", fill: "forwards" },
    );
    closeAnimationRef.current = animation;
    void animation.finished.then(() => onClose(), () => {});
  }, [onClose]);
  useEffect(() => () => {
    closeAnimationRef.current?.cancel();
    closeAnimationRef.current = null;
  }, [selected?.childRunId, selected?.rootRunId]);
  useEffect(() => {
    if (selected) titleRef.current?.focus();
  }, [selected?.childRunId, selected?.rootRunId]);
  useEffect(() => {
    if (!selected) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) {
        event.preventDefault();
        closePanel();
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [selected, closePanel]);
  const key = ["studio", "child-timeline", selected?.rootRunId, selected?.childRunId];
  const query = useQuery({
    queryKey: key,
    queryFn: () => loadTimeline(selected?.rootRunId as string, selected?.childRunId as string, queryClient.getQueryData<AutoChildTimeline>(key)),
    enabled: Boolean(selected),
    refetchInterval: (current) => {
      const data = current.state.data;
      if (!data) return false;
      if (!TERMINAL.has(data.run.status)) return 1500;
      const terminalType = {
        completed: "agent_completed", failed: "agent_failed",
        cancelled: "agent_cancelled", interrupted: "agent_interrupted",
      }[data.run.status];
      const hasTerminal = data.events.some((event) => event.type === terminalType);
      const hasFinalAnswer = data.run.status !== "completed" || !data.run.result_summary
        || data.events.some((event) => event.type === "child_activity" && event.payload.event_type === "answer");
      if (hasTerminal && hasFinalAnswer) return false;
      const catchUp = terminalCatchUpRef.current;
      const key = `${selected?.rootRunId}:${selected?.childRunId}`;
      if (catchUp.key !== key) {
        catchUp.key = key;
        catchUp.updatedAt = 0;
        catchUp.startedAt = Date.now();
        catchUp.attempts = 0;
        catchUp.observed = false;
      }
      if (catchUp.updatedAt !== current.state.dataUpdatedAt) {
        if (catchUp.observed) catchUp.attempts += 1;
        catchUp.updatedAt = current.state.dataUpdatedAt;
        catchUp.observed = true;
      }
      return catchUp.attempts < 4 && Date.now() - catchUp.startedAt < 6000 ? 750 : false;
    },
  });
  const run = query.data?.run, events = useMemo(() => query.data?.events ?? [], [query.data?.events]);
  const selectedRun = runs.find((item) => item.run_id === selected?.childRunId);
  const childName = run?.agent_name || selectedRun?.agent_name || agents.find((agent) => agent.agent_id === selectedRun?.agent_id)?.name || "Subagent";
  const steps = useMemo(
    () => run ? stepsFrom(events, run.root_run_id, childName, TERMINAL.has(run.status), ["failed", "cancelled", "interrupted"].includes(run.status)) : [],
    [events, run, childName],
  );
  const shownAnswer = steps.some((step) => step.kind === "answer");
  if (!selected) return null;
  return <aside
    ref={panelRef}
    aria-label={`Subagent conversation: ${childName}`}
    data-testid="subagent-panel"
    className="nova-subagent-panel flex min-h-0 min-w-0 w-full flex-1 flex-col overflow-hidden border-l border-border bg-background lg:w-[min(48rem,52%)] lg:flex-none"
  >
      <header className="flex min-h-12 min-w-0 shrink-0 items-center gap-2 border-b border-border px-2 sm:px-3">
        <Button type="button" variant="ghost" size="icon" aria-label="Back to conversation" onClick={closePanel} className="size-11 shrink-0 rounded-lg text-muted-foreground hover:text-foreground sm:size-8"><ArrowLeft aria-hidden="true" className="size-4" /></Button>
        <h2 ref={titleRef} tabIndex={-1} className="min-w-0 flex-1 truncate text-sm font-medium outline-none">{childName}</h2>
        {run ? <Status status={run.status} /> : null}
        <Button type="button" variant="ghost" size="icon" aria-label="Close subagent panel" onClick={closePanel} className="size-11 shrink-0 rounded-lg text-muted-foreground hover:text-foreground sm:size-8"><X aria-hidden="true" className="size-4" /></Button>
      </header>
      <div aria-label="Subagent timeline" className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-5 sm:px-6">
        {query.isLoading ? <p role="status" className="text-sm text-muted-foreground">Loading subagent conversation…</p> : null}
        {query.isError ? <div role="alert" className="text-sm text-muted-foreground">Could not load the conversation. <Button variant="link" size="sm" className="h-auto px-0" onClick={() => void query.refetch()}>Retry</Button></div> : null}
        {run ? <div className="mx-auto flex max-w-3xl flex-col gap-5">
          <section aria-label="Assigned task" className="border-l-2 border-border pl-3">
            <p className="text-xs text-muted-foreground">Assigned task</p>
            <p className="mt-1 break-words whitespace-pre-wrap text-sm leading-relaxed">{run.objective}</p>
          </section>
          {steps.length ? <Timeline steps={steps} /> : <p className="text-sm text-muted-foreground">{TERMINAL.has(run.status) ? "No activity was recorded." : "Starting"}</p>}
          {run.status === "completed" && !shownAnswer && run.result_summary ? <article className="rounded-xl border border-border bg-card px-4 py-3"><p className="mb-2 text-xs font-medium text-muted-foreground">Answer</p><Markdown className="text-sm leading-relaxed">{run.result_summary}</Markdown></article> : null}
        </div> : null}
      </div>
    </aside>;
}

export function AutoSubagentCard({ runs, agents, loading, error, retry, onSelectChild, embedded = false }: CardProps) {
  const root = runs.find((run) => run.depth === 0), children = latestParticipants(runs).filter((run) => run.depth > 0);
  if (!root && !loading && !error) return null;
  const body = <Card root={root} children={children} agents={agents} loading={loading} error={error} retry={retry} onSelectChild={onSelectChild} />;
  if (embedded) return <div className="flex min-h-0 min-w-0 flex-col">{body}</div>;
  return <><div className="absolute right-4 top-4 z-20 hidden h-1/3 min-h-0 w-72 flex-col xl:flex">{body}</div><div className="flex max-h-1/3 min-h-0 shrink-0 flex-col overflow-hidden px-4 pt-3 sm:px-6 xl:hidden"><div className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col">{body}</div></div></>;
}
