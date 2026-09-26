import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowUp, ChevronRight, Square, Workflow, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/features/assistant/markdown";
import { agentsApi, type Agent, type AutoChildTimeline, type AutoRun, type AutoRunEvent } from "@/features/agents/api";
import { cn } from "@/lib/utils";
import { latestParticipants } from "./smart-agent-tree";

type SelectedChild = { rootRunId: string; childRunId: string };
type CardProps = {
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

function ChildEvent({ event, rootRunId, childName, hasAnswer, toolNames }: { event: AutoRunEvent; rootRunId: string; childName: string; hasAnswer: boolean; toolNames: Map<string, string> }) {
  const payload = event.payload;
  if (event.type === "agent_queued") return null;
  if (event.type === "agent_message") {
    const sender = String(payload.sender_run_id ?? ""), recipient = String(payload.recipient_run_id ?? "");
    if (sender !== event.run_id && recipient !== event.run_id) return null;
    const label = payload.origin === "user" ? `You to ${childName}` : sender === rootRunId ? `Smart to ${childName}` : `${String(payload.sender_agent_path || sender)} to ${String(payload.recipient_agent_path || recipient)}`;
    return <article className={cn("min-w-0 rounded-xl border px-4 py-3", sender === rootRunId ? "ml-5 border-border bg-surface-1" : "mr-5 border-border bg-card")}><p className="mb-1 text-xs font-medium text-muted-foreground">{label}</p><p className="break-words whitespace-pre-wrap text-sm leading-relaxed">{String(payload.content ?? "")}</p></article>;
  }
  if (event.type === "child_activity") {
    const kind = String(payload.event_type ?? ""), text = typeof payload.text === "string" ? payload.text : "";
    if (kind === "omitted" && payload.reason === "activity_limit") return <p role="status" className="border-l border-border pl-4 text-xs text-muted-foreground">Additional activity was omitted after this run reached its event limit.</p>;
    if (kind === "answer") return text ? <article className="min-w-0 rounded-xl border border-border bg-card px-4 py-3"><p className="mb-2 text-xs font-medium text-muted-foreground">{childName}</p><Markdown className="text-sm leading-relaxed">{text}</Markdown></article> : null;
    if (kind === "plan") {
      const steps = Array.isArray(payload.steps) ? payload.steps : [];
      return steps.length ? <div className="min-w-0 border-l border-border pl-4 text-sm text-muted-foreground"><span className="mb-1 block text-xs font-medium">Plan</span><ol className="list-inside list-decimal space-y-1">{steps.map((step, index) => <li key={`${event.event_id}-${index}`} className="break-words">{typeof step === "object" && step ? String((step as Record<string, unknown>).text ?? "") : ""}</li>)}</ol></div> : null;
    }
    if (["thinking", "plan"].includes(kind) && text) return <p className="text-xs text-muted-foreground">Working</p>;
    if (["tool_call", "tool_progress", "tool_detail", "tool_status"].includes(kind)) {
      const toolId = String(payload.tool_call_id ?? "");
      const tool = String(payload.tool_name || toolNames.get(toolId) || "Tool");
      const status = typeof payload.status === "string" ? payload.status : "";
      const preview = typeof payload.sql_preview === "string" ? payload.sql_preview : "";
      const detail = text || String(payload.result_summary ?? "") || String(payload.stage ?? "");
      return <div className="min-w-0 rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm"><div className="flex items-center gap-2"><Workflow aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" /><span className="min-w-0 flex-1 break-words font-medium">{tool}</span>{status ? <span className="text-xs text-muted-foreground">{status}</span> : null}</div>{detail ? <p className="mt-1 break-words text-muted-foreground">{detail}</p> : null}{preview ? <pre className="mt-2 max-h-52 min-w-0 overflow-auto rounded-md bg-background p-2 text-xs"><code>{preview}</code></pre> : null}</div>;
    }
    if (kind === "error") return <p role="alert" className="text-sm text-destructive">{String(payload.message ?? "The subagent stopped with an error.")}</p>;
    return null;
  }
  if (event.type === "tool_activity") return [...toolNames.values()].includes(String(payload.tool_name ?? "")) ? null : <p className="text-sm text-muted-foreground">Called {String(payload.tool_name ?? "tool")}</p>;
  if (event.type === "agent_completed") {
    const summary = String(payload.summary ?? "");
    return !hasAnswer && summary ? <article className="min-w-0 rounded-xl border border-border bg-card px-4 py-3"><p className="mb-2 text-xs font-medium text-muted-foreground">{childName}</p><Markdown className="text-sm leading-relaxed">{summary}</Markdown></article> : <p className="text-xs text-muted-foreground">{childName} finished.</p>;
  }
  if (["agent_failed", "agent_cancelled", "agent_interrupted"].includes(event.type)) return <p role="status" className="text-sm text-destructive">{childName} {event.type.slice(6).replace(/_/g, " ")}.</p>;
  if (event.type === "agent_started") return <p className="text-xs text-muted-foreground">{childName} started working.</p>;
  if (event.type === "agent_waiting") return <p className="text-xs text-muted-foreground">Waiting for a message.</p>;
  if (event.type === "agent_resumed") return <p className="text-xs text-muted-foreground">Resumed work.</p>;
  return null;
}

export function AutoSubagentPanel({ selected, runs, agents, onClose, onRefreshTree }: PanelProps) {
  const queryClient = useQueryClient();
  const panelRef = useRef<HTMLElement>(null);
  const closeAnimationRef = useRef<Animation | null>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const terminalCatchUpRef = useRef({ key: "", updatedAt: 0, startedAt: 0, attempts: 0, observed: false });
  const [message, setMessage] = useState(""), [sending, setSending] = useState(false), [actionError, setActionError] = useState<string | null>(null);
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
    setMessage("");
    setActionError(null);
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
  const run = query.data?.run, active = run && !TERMINAL.has(run.status), events = query.data?.events ?? [];
  const selectedRun = runs.find((item) => item.run_id === selected?.childRunId);
  const selectedRoot = selectedRun?.depth === 0;
  const childName = run?.agent_name || selectedRun?.agent_name || agents.find((agent) => agent.agent_id === selectedRun?.agent_id)?.name || "Subagent";
  const root = runs.find((item) => item.depth === 0);
  const canFollowup = Boolean(run && !selectedRoot && run.status !== "cancelled" && selectedRun?.status !== "cancelled" && root?.agent_id === "__smart__" && !TERMINAL.has(root.status));
  const hasAnswer = events.some((event) => event.type === "child_activity" && event.payload.event_type === "answer");
  const toolNames = new Map(events.filter((event) => event.type === "child_activity" && event.payload.event_type === "tool_call").map((event) => [String(event.payload.tool_call_id ?? ""), String(event.payload.tool_name ?? "Tool")]));
  const refresh = () => { void query.refetch(); void queryClient.invalidateQueries({ queryKey: ["studio", "auto-events", selected?.rootRunId] }); onRefreshTree(); };
  const send = async () => {
    const content = message.trim();
    if (!selected || (!active && !canFollowup) || !content || sending) return;
    setSending(true); setActionError(null);
    try {
      if (active) await agentsApi.sendAutoChildMessage(selected.rootRunId, selected.childRunId, { operation_id: crypto.randomUUID(), content });
      else await agentsApi.followupSmartAgent(selected.rootRunId, selectedRun?.agent_session_id ?? selected.childRunId, content, crypto.randomUUID());
      setMessage(""); refresh();
    }
    catch { setActionError("Could not send the message. Try again."); }
    finally { setSending(false); }
  };
  const cancel = async () => {
    if (!selected || !active) return;
    setActionError(null);
    try {
      if (selectedRoot) await agentsApi.cancelAutoRun(selected.rootRunId);
      else await agentsApi.cancelAutoChild(selected.rootRunId, selected.childRunId);
      refresh();
    }
    catch { setActionError("Could not cancel the subagent. Try again."); }
  };
  const interrupt = async () => {
    if (!selected || !active) return;
    setActionError(null);
    try { await agentsApi.interruptSmartAgent(selected.rootRunId, selectedRun?.agent_session_id ?? selected.childRunId); refresh(); }
    catch { setActionError("Could not interrupt this agent. Try again."); }
  };
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
        {run ? <div className="mx-auto flex max-w-3xl flex-col gap-4"><article className="ml-5 rounded-xl border border-border bg-surface-1 px-4 py-3"><p className="mb-1 text-xs font-medium text-muted-foreground">Assigned task</p><p className="break-words whitespace-pre-wrap text-sm leading-relaxed">{run.objective}</p></article>
          {events.map((event) => <ChildEvent key={event.event_id} event={event} rootRunId={run.root_run_id} childName={run.agent_name} hasAnswer={hasAnswer} toolNames={toolNames} />)}
          {events.length === 0 ? <p className="text-sm text-muted-foreground">No activity recorded yet.</p> : null}
          {run.status === "completed" && !hasAnswer && !events.some((event) => event.type === "agent_completed") && run.result_summary ? <article className="rounded-xl border border-border bg-card px-4 py-3"><p className="mb-2 text-xs font-medium text-muted-foreground">{run.agent_name}</p><Markdown className="text-sm leading-relaxed">{run.result_summary}</Markdown></article> : null}
        </div> : null}
      </div>
      <div className="shrink-0 border-t border-border bg-background px-4 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-4 sm:px-6">
        {actionError ? <p role="alert" className="mb-2 text-sm text-destructive">{actionError}</p> : null}
        {active || canFollowup ? <div className="mx-auto max-w-3xl"><div className="rounded-xl border border-border bg-card focus-within:ring-2 focus-within:ring-ring"><textarea aria-label="Message subagent" placeholder={active ? "Send additional context" : "Give this specialist a follow-up task"} value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void send(); } }} rows={2} maxLength={4000} className="w-full resize-none bg-transparent px-3 pt-3 text-sm outline-none placeholder:text-muted-foreground" /><div className="flex flex-wrap items-center justify-between gap-2 px-2 pb-2">{active ? <>{!selectedRoot ? <Button type="button" variant="ghost" size="sm" onClick={() => void interrupt()} className="min-h-11">Interrupt</Button> : null}<Button type="button" variant="ghost" size="sm" onClick={() => void cancel()} className="min-h-11 text-muted-foreground hover:text-destructive"><Square aria-hidden="true" className="mr-2 size-3.5" />Cancel agent</Button></> : <span className="text-xs text-muted-foreground">Starts another turn in this session</span>}<Button type="button" size="icon" aria-label={active ? "Send to subagent" : "Start follow-up"} disabled={!message.trim() || sending} onClick={() => void send()} className="size-11"><ArrowUp aria-hidden="true" className="size-4" /></Button></div></div></div>
          : run ? <p className="text-sm text-muted-foreground">This conversation is read only.</p> : null}
      </div>
    </aside>;
}

export function AutoSubagentCard({ runs, agents, loading, error, retry, onSelectChild }: CardProps) {
  const root = runs.find((run) => run.depth === 0), children = latestParticipants(runs).filter((run) => run.depth > 0);
  if (!root && !loading && !error) return null;
  const body = <Card root={root} children={children} agents={agents} loading={loading} error={error} retry={retry} onSelectChild={onSelectChild} />;
  return <><div className="absolute right-4 top-4 z-20 hidden h-1/3 min-h-0 w-72 flex-col xl:flex">{body}</div><div className="flex max-h-1/3 min-h-0 shrink-0 flex-col overflow-hidden px-4 pt-3 sm:px-6 xl:hidden"><div className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col">{body}</div></div></>;
}
