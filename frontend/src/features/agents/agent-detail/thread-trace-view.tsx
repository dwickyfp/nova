import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Bot,
  Check,
  ChevronDown,
  ChevronLeft,
  CircleAlert,
  Clock3,
  Coins,
  Database,
  FileJson,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Route,
  Sparkles,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import type { AgentMessage } from "@/features/agents/api";
import {
  observabilityApi,
  type ThreadTrace,
  type ThreadTurn,
  type TraceStep,
} from "@/features/agents/api";
import {
  replayThread,
  type TranscriptTurn,
} from "@/features/studio/studio-chat";
import { ProcessRail } from "@/features/studio/thought-turn";
import { TurnContent } from "@/features/studio/turn-content";
import { cn } from "@/lib/utils";

type Pane = "conversation" | "thread" | "detail";
type DetailKind =
  | "turn"
  | "provider"
  | "semantic"
  | "sql"
  | "chart"
  | "tool"
  | "response"
  | "context"
  | "reasoning";

type TraceNode = {
  id: string;
  turnId: string;
  label: string;
  description?: string;
  status: "done" | "failed" | "running";
  depth: number;
  detailKind: DetailKind;
  startedOffsetMs?: number;
  durationMs?: number;
  step?: TraceStep;
  toolCallId?: string;
};

type TraceGroup = {
  id: string;
  index: number;
  question: string;
  assistant: ThreadTurn;
  transcript: TranscriptTurn;
  nodes: TraceNode[];
  durationMs?: number;
  contentNode: Map<string, string>;
};

type Selection = {
  turnId: string;
  nodeId: string;
  contentId?: string;
};

/** Linked, read-only observability view for a persisted agent conversation. */
export function ThreadTraceView({
  agentId,
  threadId,
  onClose,
}: {
  agentId: string;
  threadId: string;
  onClose: () => void;
}) {
  const traceQuery = useQuery({
    queryKey: ["agents", "observability", "trace", agentId, threadId],
    queryFn: () => observabilityApi.trace(agentId, threadId),
  });
  const trace = traceQuery.data;
  const groups = useMemo(() => buildTraceGroups(trace?.turns ?? []), [trace]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [mobilePane, setMobilePane] = useState<Pane>("conversation");
  const [collapsedPanes, setCollapsedPanes] = useState<Record<Pane, boolean>>({
    conversation: false,
    thread: false,
    detail: false,
  });
  const timelineRefs = useRef(new Map<string, HTMLButtonElement>());
  const conversationRefs = useRef(new Map<string, HTMLDivElement>());

  useEffect(() => {
    if (!groups.length) return;
    setSelection((current) => {
      if (current && groups.some((group) => group.id === current.turnId))
        return current;
      return { turnId: groups[0].id, nodeId: groups[0].nodes[0].id };
    });
  }, [groups]);

  useEffect(() => {
    if (!selection) return;
    timelineRefs.current
      .get(selection.nodeId)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selection]);

  const selectedTurnId = selection?.turnId;
  useEffect(() => {
    if (!selectedTurnId) return;
    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    conversationRefs.current.get(selectedTurnId)?.scrollIntoView({
      block: "start",
      behavior: reduceMotion ? "auto" : "smooth",
    });
  }, [selectedTurnId]);

  if (traceQuery.isLoading || !trace) {
    return (
      <FullScreen>
        <Skeleton className="m-6 min-h-0 flex-1" />
      </FullScreen>
    );
  }
  if (traceQuery.isError) {
    return (
      <FullScreen>
        <div className="flex h-full items-center justify-center">
          <div className="max-w-sm text-center">
            <p className="text-sm font-medium">
              Could not load this conversation
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              The trace request failed. Close this and try again.
            </p>
            <Button variant="outline" className="mt-4" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
      </FullScreen>
    );
  }

  const activeGroup =
    groups.find((group) => group.id === selection?.turnId) ?? groups[0];
  const activeNode =
    activeGroup?.nodes.find((node) => node.id === selection?.nodeId) ??
    activeGroup?.nodes[0];
  const select = (next: Selection, pane?: Pane) => {
    setSelection(next);
    if (pane) setMobilePane(pane);
  };
  const togglePane = (pane: Pane) =>
    setCollapsedPanes((current) => ({
      ...current,
      [pane]: !current[pane],
    }));

  return (
    <FullScreen>
      <TraceHeader trace={trace} onClose={onClose} />
      <div className="flex shrink-0 gap-1 border-b px-4 py-2 lg:hidden">
        {(["conversation", "thread", "detail"] as const).map((pane) => (
          <button
            key={pane}
            type="button"
            onClick={() => setMobilePane(pane)}
            className={cn(
              "rounded-md px-3 py-1.5 text-xs capitalize transition-colors",
              mobilePane === pane
                ? "bg-accent font-medium text-foreground"
                : "text-muted-foreground hover:bg-muted",
            )}
          >
            {pane === "thread" ? "Thread details" : pane}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1 overflow-x-auto">
        <div
          data-testid="conversation-pane-shell"
          data-collapsed={collapsedPanes.conversation}
          className={cn(
            "nova-trace-pane min-h-0 flex-1 flex-col lg:flex",
            mobilePane === "conversation" ? "flex" : "hidden",
          )}
        >
          <CollapsedPaneRail
            label="Conversation"
            side="left"
            onExpand={() => togglePane("conversation")}
          />
          <div className="nova-trace-pane-content flex min-h-0 flex-1 flex-col">
            <ConversationPane
              groups={groups}
              selection={selection}
              refs={conversationRefs}
              onSelect={(next) => select(next, "thread")}
              onCollapse={() => togglePane("conversation")}
            />
          </div>
        </div>
        <div
          data-testid="thread-pane-shell"
          data-collapsed={collapsedPanes.thread}
          className={cn(
            "nova-trace-pane min-h-0 flex-1 flex-col lg:flex",
            mobilePane === "thread" ? "flex" : "hidden",
          )}
        >
          <CollapsedPaneRail
            label="Thread details"
            side="left"
            onExpand={() => togglePane("thread")}
          />
          <div className="nova-trace-pane-content flex min-h-0 flex-1 flex-col">
            <ThreadDetailsPane
              trace={trace}
              groups={groups}
              selection={selection}
              refs={timelineRefs}
              onSelect={(next) => select(next, "detail")}
              onCollapse={() => togglePane("thread")}
            />
          </div>
        </div>
        <div
          data-testid="detail-pane-shell"
          data-collapsed={collapsedPanes.detail}
          className={cn(
            "nova-trace-pane min-h-0 flex-1 flex-col lg:flex",
            mobilePane === "detail" ? "flex" : "hidden",
          )}
        >
          <CollapsedPaneRail
            label="Trace detail"
            side="right"
            onExpand={() => togglePane("detail")}
          />
          <div className="nova-trace-pane-content flex min-h-0 flex-1 flex-col">
            <StepDetailPane
              group={activeGroup}
              node={activeNode}
              onCollapse={() => togglePane("detail")}
            />
          </div>
        </div>
      </div>
    </FullScreen>
  );
}

function FullScreen({ children }: { children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background">
      {children}
    </div>
  );
}

function TraceHeader({
  trace,
  onClose,
}: {
  trace: ThreadTrace;
  onClose: () => void;
}) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b px-4">
      <div className="flex min-w-0 items-center gap-2">
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Back">
          <ChevronLeft className="size-4" />
        </Button>
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{trace.title}</div>
          <div className="truncate font-mono text-xs text-muted-foreground">
            {trace.thread_id}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-4 text-xs text-muted-foreground">
        <span className="hidden sm:inline">{trace.user_name}</span>
        <span className="flex items-center gap-1 tabular-nums">
          <Coins className="size-3.5" />
          {trace.total_tokens.toLocaleString()} tokens
        </span>
        <Button
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label="Close"
        >
          <X className="size-4" />
        </Button>
      </div>
    </header>
  );
}

function ConversationPane({
  groups,
  selection,
  refs,
  onSelect,
  onCollapse,
}: {
  groups: TraceGroup[];
  selection: Selection | null;
  refs: React.RefObject<Map<string, HTMLDivElement>>;
  onSelect: (selection: Selection) => void;
  onCollapse: () => void;
}) {
  return (
    <section className="flex min-h-0 flex-1 flex-col border-r">
      <PaneHeading
        label="Conversation"
        collapseSide="left"
        onCollapse={onCollapse}
      />
      <ScrollArea className="min-h-0 flex-1">
        <div
          data-testid="conversation-preview"
          className="nova-observability-preview mx-auto flex w-full min-w-0 max-w-3xl flex-col gap-5 px-3 py-4"
        >
          {groups.map((group) => {
            const selected = selection?.turnId === group.id;
            const responseNode = responseNodeFor(group);
            return (
              <div
                key={group.id}
                ref={(element) => {
                  if (element) refs.current.set(group.id, element);
                  else refs.current.delete(group.id);
                }}
                data-testid={`conversation-turn-${group.index + 1}`}
                data-turn-id={group.id}
                className="flex scroll-mt-4 flex-col gap-3"
              >
                {group.question ? (
                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={() =>
                        onSelect({
                          turnId: group.id,
                          nodeId: group.nodes[0].id,
                        })
                      }
                      className={cn(
                        "max-w-[85%] rounded-2xl bg-accent px-4 py-2.5 text-left text-sm text-accent-foreground ring-1 ring-input outline-none",
                        "focus-visible:ring-2 focus-visible:ring-ring",
                        selected &&
                          selection?.nodeId === group.nodes[0].id &&
                          "ring-2 ring-ring",
                      )}
                    >
                      <span className="break-words whitespace-pre-wrap">
                        {group.question}
                      </span>
                    </button>
                  </div>
                ) : null}
                <ProcessRail steps={group.transcript.steps} running={false} />
                <TurnContent
                  turn={group.transcript}
                  selectedContentId={
                    selection?.turnId === group.id ? selection.contentId : null
                  }
                  onSelectContent={(item) => {
                    const contentId = item?.id ?? "answer";
                    onSelect({
                      turnId: group.id,
                      nodeId:
                        group.contentNode.get(contentId) ??
                        responseNode?.id ??
                        group.nodes[0].id,
                      contentId,
                    });
                  }}
                />
                {group.assistant.model_name || group.assistant.total_tokens ? (
                  <p className="flex gap-2 text-xs text-muted-foreground">
                    {group.assistant.model_name ? (
                      <span>{group.assistant.model_name}</span>
                    ) : null}
                    {group.assistant.total_tokens ? (
                      <span className="tabular-nums">
                        {group.assistant.total_tokens.toLocaleString()} tokens
                      </span>
                    ) : null}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </section>
  );
}

function ThreadDetailsPane({
  trace,
  groups,
  selection,
  refs,
  onSelect,
  onCollapse,
}: {
  trace: ThreadTrace;
  groups: TraceGroup[];
  selection: Selection | null;
  refs: React.RefObject<Map<string, HTMLButtonElement>>;
  onSelect: (selection: Selection) => void;
  onCollapse: () => void;
}) {
  const traceCount = groups.reduce(
    (sum, group) => sum + group.nodes.length - 1,
    0,
  );
  const totalDuration = groups.reduce(
    (sum, group) => sum + (group.durationMs ?? 0),
    0,
  );
  return (
    <section className="flex min-h-0 flex-1 flex-col border-r">
      <PaneHeading
        label="Thread details"
        collapseSide="left"
        onCollapse={onCollapse}
      />
      <ScrollArea className="min-h-0 flex-1">
        <div className="border-b px-4 py-3">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
            <dt className="text-muted-foreground">Thread ID</dt>
            <dd className="truncate text-right font-mono">{trace.thread_id}</dd>
            <dt className="text-muted-foreground">Timestamp</dt>
            <dd className="text-right tabular-nums">
              {formatTimestamp(trace.created_at)}
            </dd>
            <dt className="text-muted-foreground">Total traces</dt>
            <dd className="text-right tabular-nums">{traceCount}</dd>
            <dt className="text-muted-foreground">Duration</dt>
            <dd className="text-right tabular-nums">
              {totalDuration > 0
                ? formatDuration(totalDuration)
                : "Not recorded"}
            </dd>
          </dl>
        </div>
        <div className="py-2">
          {groups.map((group) => (
            <div key={group.id} className="border-b py-2 last:border-b-0">
              {group.nodes.map((node) => (
                <TimelineRow
                  key={node.id}
                  node={node}
                  turnDuration={group.durationMs}
                  selected={selection?.nodeId === node.id}
                  ref={(element) => {
                    if (element) refs.current.set(node.id, element);
                    else refs.current.delete(node.id);
                  }}
                  onClick={() =>
                    onSelect({ turnId: group.id, nodeId: node.id })
                  }
                />
              ))}
            </div>
          ))}
        </div>
      </ScrollArea>
    </section>
  );
}

function TimelineRow({
  node,
  turnDuration,
  selected,
  ref,
  onClick,
}: {
  node: TraceNode;
  turnDuration?: number;
  selected: boolean;
  ref: (element: HTMLButtonElement | null) => void;
  onClick: () => void;
}) {
  const Icon = nodeIcon(node);
  const left =
    turnDuration && node.startedOffsetMs != null
      ? Math.min(100, (node.startedOffsetMs / turnDuration) * 100)
      : 0;
  const width =
    turnDuration && node.durationMs != null
      ? Math.max(
          2,
          Math.min(100 - left, (node.durationMs / turnDuration) * 100),
        )
      : 0;
  return (
    <button
      ref={ref}
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "grid min-h-9 w-full grid-cols-[minmax(0,1fr)_4rem_5.5rem] items-center gap-2 px-3 text-left text-xs outline-none transition-colors",
        "hover:bg-muted/60 focus-visible:bg-muted focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        selected && "bg-accent text-accent-foreground",
      )}
    >
      <span
        className="relative flex min-w-0 items-center gap-2"
        style={{ paddingLeft: `${node.depth * 18}px` }}
      >
        {node.depth > 0 ? (
          <span
            className="absolute top-0 bottom-0 border-l"
            style={{ left: `${node.depth * 18 - 9}px` }}
          />
        ) : null}
        <span
          className={cn(
            "grid size-5 shrink-0 place-items-center rounded-sm border bg-background",
            node.status === "failed" &&
              "border-destructive/50 text-destructive",
          )}
        >
          <Icon className="size-3" />
        </span>
        <span className="min-w-0">
          <span className="block truncate font-medium">{node.label}</span>
          {node.description ? (
            <span className="block truncate text-[11px] text-muted-foreground">
              {node.description}
            </span>
          ) : null}
        </span>
      </span>
      <span className="text-right tabular-nums text-muted-foreground">
        {node.durationMs != null ? formatDuration(node.durationMs) : "—"}
      </span>
      <span className="relative h-1.5 overflow-hidden rounded-full bg-muted">
        {width > 0 ? (
          <span
            className={cn(
              "absolute top-0 h-full rounded-full",
              node.status === "failed" ? "bg-destructive" : "bg-info-strong",
            )}
            style={{ left: `${left}%`, width: `${width}%` }}
          />
        ) : null}
      </span>
    </button>
  );
}

function StepDetailPane({
  group,
  node,
  onCollapse,
}: {
  group?: TraceGroup;
  node?: TraceNode;
  onCollapse: () => void;
}) {
  if (!group || !node) {
    return (
      <section className="flex min-h-0 flex-col">
        <PaneHeading
          label="Trace detail"
          collapseSide="right"
          onCollapse={onCollapse}
        />
        <p className="p-4 text-sm text-muted-foreground">
          Select a trace step.
        </p>
      </section>
    );
  }
  const detail = toolTraceDetail(node.step);
  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <PaneHeading
        label={node.label}
        icon={node.detailKind === "semantic"}
        collapseSide="right"
        onCollapse={onCollapse}
      />
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 p-4">
          <StepSummary group={group} node={node} />
          {node.detailKind === "semantic" || node.detailKind === "sql" ? (
            <SemanticDetail
              detail={detail}
              showSql={node.detailKind === "sql"}
            />
          ) : null}
          {node.detailKind === "turn" ? (
            <>
              <Panel title="Input">
                <DetailField label="User question">
                  <p className="whitespace-pre-wrap">
                    {group.question || "Not recorded"}
                  </p>
                </DetailField>
              </Panel>
              <Panel title="System instructions" defaultOpen={false}>
                {group.assistant.instructions ? (
                  <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-muted/40 p-3 font-mono text-xs">
                    {group.assistant.instructions}
                  </pre>
                ) : (
                  <EmptyDetail>Not recorded for this turn.</EmptyDetail>
                )}
              </Panel>
            </>
          ) : null}
          {node.detailKind === "provider" ? (
            <Panel title="Model call">
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs">
                <dt className="text-muted-foreground">Purpose</dt>
                <dd className="text-right capitalize">
                  {node.step?.kind === "provider"
                    ? node.step.purpose
                    : "planning"}
                </dd>
                <dt className="text-muted-foreground">Model</dt>
                <dd className="text-right">
                  {group.assistant.model_name ?? "Not recorded"}
                </dd>
                <dt className="text-muted-foreground">Status</dt>
                <dd className="text-right capitalize">{node.status}</dd>
              </dl>
            </Panel>
          ) : null}
          {node.detailKind === "tool" || node.detailKind === "chart" ? (
            <ToolDetail step={node.step} />
          ) : null}
          {node.detailKind === "reasoning" &&
          node.step?.kind === "reasoning" ? (
            <Panel title="Recorded narration">
              <p className="whitespace-pre-wrap text-sm leading-relaxed">
                {node.step.text}
              </p>
            </Panel>
          ) : null}
          {node.detailKind === "context" ? (
            <Panel title="Context management">
              <pre className="overflow-auto rounded-md bg-muted/40 p-3 font-mono text-xs">
                {JSON.stringify(node.step, null, 2)}
              </pre>
            </Panel>
          ) : null}
          {node.detailKind === "response" ? (
            <>
              <Panel title="Output">
                <TurnContent turn={group.transcript} />
              </Panel>
              <Panel title="Usage" defaultOpen={false}>
                <dl className="grid grid-cols-2 gap-2 text-xs">
                  <dt className="text-muted-foreground">Prompt tokens</dt>
                  <dd className="text-right tabular-nums">
                    {group.assistant.prompt_tokens ?? "—"}
                  </dd>
                  <dt className="text-muted-foreground">Completion tokens</dt>
                  <dd className="text-right tabular-nums">
                    {group.assistant.completion_tokens ?? "—"}
                  </dd>
                  <dt className="text-muted-foreground">Total tokens</dt>
                  <dd className="text-right tabular-nums">
                    {group.assistant.total_tokens ?? "—"}
                  </dd>
                </dl>
              </Panel>
            </>
          ) : null}
        </div>
      </ScrollArea>
    </section>
  );
}

function StepSummary({ group, node }: { group: TraceGroup; node: TraceNode }) {
  return (
    <div className="border-b pb-3">
      <div className="flex items-center justify-between gap-3">
        <Badge variant={node.status === "failed" ? "destructive" : "secondary"}>
          {node.status}
        </Badge>
        <span className="text-xs tabular-nums text-muted-foreground">
          {node.durationMs != null
            ? formatDuration(node.durationMs)
            : "Duration not recorded"}
        </span>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Turn {group.index + 1} · {formatTimestamp(group.assistant.created_at)}
      </p>
    </div>
  );
}

function SemanticDetail({
  detail,
  showSql,
}: {
  detail: Record<string, unknown> | null;
  showSql: boolean;
}) {
  if (!detail)
    return (
      <EmptyDetail>
        Semantic context was not recorded for this legacy trace.
      </EmptyDetail>
    );
  const model = objectValue(detail.semantic_model);
  const datasets = arrayOfObjects(detail.datasets);
  const metrics = stringArray(detail.metrics);
  const warnings = stringArray(detail.validation_warnings);
  const sql = stringValue(detail.generated_sql);
  return (
    <>
      <Panel title="Input">
        <DetailField label="User question">
          <p className="whitespace-pre-wrap">
            {stringValue(detail.question) || "Not recorded"}
          </p>
        </DetailField>
      </Panel>
      <Panel title="Semantic model">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs">
          <dt className="text-muted-foreground">Name</dt>
          <dd className="text-right">
            {stringValue(model?.name) || "Not recorded"}
          </dd>
          <dt className="text-muted-foreground">Model ID</dt>
          <dd className="truncate text-right font-mono">
            {stringValue(model?.id) || "—"}
          </dd>
          <dt className="text-muted-foreground">Ossie version</dt>
          <dd className="text-right">
            {stringValue(model?.ossie_version) || "—"}
          </dd>
          <dt className="text-muted-foreground">Confidence</dt>
          <dd className="text-right tabular-nums">
            {numberValue(detail.confidence) ?? "—"}
          </dd>
        </dl>
      </Panel>
      <Panel title="Tables">
        {datasets.length ? (
          <div className="space-y-2">
            {datasets.map((dataset, index) => (
              <div
                key={`${stringValue(dataset.name)}-${index}`}
                className="rounded-md border px-3 py-2 text-xs"
              >
                <p className="font-medium">
                  {stringValue(dataset.name) || "Dataset"}
                </p>
                <p className="mt-0.5 break-all font-mono text-muted-foreground">
                  {stringValue(dataset.source) || "Source not recorded"}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <EmptyDetail>No datasets were recorded.</EmptyDetail>
        )}
      </Panel>
      {metrics.length ? (
        <Panel title="Metrics" defaultOpen={false}>
          <div className="flex flex-wrap gap-1.5">
            {metrics.map((metric) => (
              <Badge key={metric} variant="outline">
                {metric}
              </Badge>
            ))}
          </div>
        </Panel>
      ) : null}
      {(showSql || sql) && sql ? (
        <Panel title="Generated SQL">
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-muted/40 p-3 font-mono text-xs">
            {sql}
          </pre>
        </Panel>
      ) : null}
      <Panel title="Validation warnings" defaultOpen={warnings.length > 0}>
        {warnings.length ? (
          <ul className="list-disc space-y-1 pl-4 text-xs">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : (
          <EmptyDetail>No validation warnings.</EmptyDetail>
        )}
      </Panel>
      {stringValue(detail.error) ? (
        <Panel title="Error">
          <p className="text-sm text-destructive">
            {stringValue(detail.error)}
          </p>
        </Panel>
      ) : null}
    </>
  );
}

function ToolDetail({ step }: { step?: TraceStep }) {
  if (!step || step.kind !== "tool") return null;
  return (
    <>
      <Panel title="Tool call">
        <dl className="space-y-3 text-xs">
          <DetailField label="Tool">
            <span className="font-mono">{step.name}</span>
          </DetailField>
          {step.preview ? (
            <DetailField label="Preview">
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted/40 p-3 font-mono text-xs">
                {step.preview}
              </pre>
            </DetailField>
          ) : null}
          {Object.keys(step.arguments ?? {}).length ? (
            <DetailField label="Arguments">
              <dl className="space-y-1">
                {Object.entries(step.arguments).map(([key, value]) => (
                  <div key={key} className="grid grid-cols-[auto_1fr] gap-3">
                    <dt className="font-mono text-muted-foreground">{key}</dt>
                    <dd className="min-w-0 break-words text-right">{value}</dd>
                  </div>
                ))}
              </dl>
            </DetailField>
          ) : null}
          {step.detail ? (
            <DetailField label="Result">
              <p className="whitespace-pre-wrap">{step.detail}</p>
            </DetailField>
          ) : null}
          {step.error ? (
            <DetailField label="Error">
              <p className="text-destructive">{step.error}</p>
            </DetailField>
          ) : null}
        </dl>
      </Panel>
      {step.progress?.length ? (
        <Panel title="Lifecycle" defaultOpen={false}>
          <ol className="space-y-2 text-xs">
            {step.progress.map((progress, index) => (
              <li
                key={`${progress.stage}-${index}`}
                className="flex items-start gap-2"
              >
                <Check className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1">
                  <span className="font-mono">{progress.stage}</span>
                  <span className="block text-muted-foreground">
                    {progress.text}
                  </span>
                </span>
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  {progress.duration_ms != null
                    ? formatDuration(progress.duration_ms)
                    : "—"}
                </span>
              </li>
            ))}
          </ol>
        </Panel>
      ) : null}
    </>
  );
}

function DetailField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="mb-1 text-xs text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}

function EmptyDetail({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-muted-foreground">{children}</p>;
}

function PaneHeading({
  label,
  icon = false,
  collapseSide,
  onCollapse,
}: {
  label: string;
  icon?: boolean;
  collapseSide: "left" | "right";
  onCollapse: () => void;
}) {
  const CollapseIcon =
    collapseSide === "left" ? PanelLeftClose : PanelRightClose;
  return (
    <div className="flex h-10 shrink-0 items-center justify-between gap-2 border-b px-2 pl-4 text-xs font-medium">
      <span className="flex min-w-0 items-center gap-2">
        {icon ? <FileJson className="size-3.5 text-muted-foreground" /> : null}
        <span className="truncate">{label}</span>
      </span>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="hidden size-7 lg:inline-flex"
        onClick={onCollapse}
        aria-label={`Collapse ${label}`}
        title={`Collapse ${label}`}
      >
        <CollapseIcon className="size-3.5" />
      </Button>
    </div>
  );
}

function CollapsedPaneRail({
  label,
  side,
  onExpand,
}: {
  label: string;
  side: "left" | "right";
  onExpand: () => void;
}) {
  const ExpandIcon = side === "left" ? PanelLeftOpen : PanelRightOpen;
  return (
    <aside className="nova-trace-pane-rail hidden min-h-0 flex-none flex-col items-center overflow-hidden border-r bg-muted/10 py-1 lg:flex">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-9 shrink-0"
        onClick={onExpand}
        aria-label={`Expand ${label}`}
        title={`Expand ${label}`}
      >
        <ExpandIcon className="size-4" />
      </Button>
      <button
        type="button"
        onClick={onExpand}
        className="mt-2 flex min-h-0 flex-1 items-start text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="[writing-mode:vertical-rl]">{label}</span>
      </button>
    </aside>
  );
}

function Panel({
  title,
  children,
  defaultOpen = true,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <Collapsible defaultOpen={defaultOpen} className="rounded-lg border">
      <CollapsibleTrigger className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
        {title}
        <ChevronDown className="size-4 text-muted-foreground transition-transform data-[state=open]:rotate-180" />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t px-3 py-3">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}

function buildTraceGroups(turns: ThreadTurn[]): TraceGroup[] {
  const groups: TraceGroup[] = [];
  let latestUser: ThreadTurn | undefined;
  for (const message of turns) {
    if (message.role === "user") {
      latestUser = message;
      continue;
    }
    if (message.role !== "assistant") continue;
    const replayMessages: AgentMessage[] = [];
    if (latestUser) replayMessages.push(toAgentMessage(latestUser));
    replayMessages.push(toAgentMessage(message));
    const transcript =
      replayThread(replayMessages)[0] ?? emptyTranscript(message);
    groups.push(buildGroup(message, latestUser, transcript, groups.length));
    latestUser = undefined;
  }
  return groups;
}

function buildGroup(
  assistant: ThreadTurn,
  user: ThreadTurn | undefined,
  transcript: TranscriptTurn,
  index: number,
): TraceGroup {
  const nodes: TraceNode[] = [];
  const rootId = `${assistant.message_id}:root`;
  const hasProvider = assistant.steps.some((step) => step.kind === "provider");
  nodes.push({
    id: rootId,
    turnId: assistant.message_id,
    label: `Agent turn ${index + 1}`,
    description: assistant.model_name ?? undefined,
    status: assistant.steps.some(isFailedStep) ? "failed" : "done",
    depth: 0,
    detailKind: "turn",
  });

  for (const [stepIndex, step] of assistant.steps.entries()) {
    const baseId = stepId(step) ?? `${assistant.message_id}:step:${stepIndex}`;
    const timing = stepTiming(step);
    if (step.kind === "provider") {
      nodes.push({
        id: baseId,
        turnId: assistant.message_id,
        label:
          step.purpose === "response"
            ? "LLM Response Generation"
            : "LLM Planning",
        status: normalizeStatus(step.status),
        depth: 1,
        detailKind: "provider",
        step,
        ...timing,
      });
      continue;
    }
    if (step.kind === "reasoning") {
      if (hasProvider && ["plan", "act", "answer"].includes(step.phase))
        continue;
      nodes.push({
        id: baseId,
        turnId: assistant.message_id,
        label: reasoningLabel(step.phase),
        description: step.text,
        status: normalizeStatus(step.status ?? "done"),
        depth: 1,
        detailKind: "reasoning",
        step,
        ...timing,
      });
      continue;
    }
    if (step.kind === "tool") {
      const detail = toolTraceDetail(step);
      const toolNode: TraceNode = {
        id: baseId,
        turnId: assistant.message_id,
        label: toolLabel(step.name),
        description: step.status_text || step.detail || undefined,
        status: normalizeStatus(step.status),
        depth: 1,
        detailKind: step.name === "data_to_chart" ? "chart" : "tool",
        step,
        toolCallId: step.tool_call_id,
        ...timing,
      };
      nodes.push(toolNode);
      if (detail?.kind === "semantic_context") {
        nodes.push({
          id: `${baseId}:semantic`,
          turnId: assistant.message_id,
          label: "Semantic Context",
          description:
            stringValue(objectValue(detail.semantic_model)?.name) || undefined,
          status: toolNode.status,
          depth: 2,
          detailKind: "semantic",
          step,
          toolCallId: step.tool_call_id,
          startedOffsetMs: timing.startedOffsetMs,
          durationMs: numberValue(detail.generation_duration_ms) ?? undefined,
        });
        if (stringValue(detail.generated_sql)) {
          nodes.push({
            id: `${baseId}:sql`,
            turnId: assistant.message_id,
            label: "SQL Execution",
            description: step.status_text || "Ran the generated query",
            status: toolNode.status,
            depth: 2,
            detailKind: "sql",
            step,
            toolCallId: step.tool_call_id,
            startedOffsetMs:
              timing.startedOffsetMs == null
                ? undefined
                : timing.startedOffsetMs +
                  (numberValue(detail.generation_duration_ms) ?? 0),
            durationMs: numberValue(detail.execution_duration_ms) ?? undefined,
          });
        }
      }
      continue;
    }
    if (step.kind === "context") {
      nodes.push({
        id: baseId,
        turnId: assistant.message_id,
        label: "Context Management",
        status: "done",
        depth: 1,
        detailKind: "context",
        step,
        ...timing,
      });
      continue;
    }
    if (step.kind === "answer" && !hasProvider) {
      nodes.push({
        id: baseId,
        turnId: assistant.message_id,
        label: "LLM Response Generation",
        status: "done",
        depth: 1,
        detailKind: "response",
        step,
        ...timing,
      });
    }
  }

  if (!nodes.some((node) => node.detailKind === "response")) {
    const responseProvider = [...nodes]
      .reverse()
      .find(
        (node) =>
          node.step?.kind === "provider" && node.step.purpose === "response",
      );
    if (responseProvider) responseProvider.detailKind = "response";
    else
      nodes.push({
        id: `${assistant.message_id}:response`,
        turnId: assistant.message_id,
        label: "LLM Response Generation",
        status: "done",
        depth: 1,
        detailKind: "response",
      });
  }

  const durationMs =
    nodes.reduce(
      (highest, node) =>
        node.startedOffsetMs == null || node.durationMs == null
          ? highest
          : Math.max(highest, node.startedOffsetMs + node.durationMs),
      0,
    ) || undefined;
  nodes[0].durationMs = durationMs;
  nodes[0].startedOffsetMs = 0;
  const contentNode = new Map<string, string>();
  const response = [...nodes]
    .reverse()
    .find((node) => node.detailKind === "response");
  contentNode.set("answer", response?.id ?? rootId);
  for (const step of assistant.steps) {
    if (step.kind === "text" && step.content_id)
      contentNode.set(step.content_id, response?.id ?? rootId);
    if (
      (step.kind === "table" ||
        step.kind === "chart" ||
        step.kind === "citation") &&
      step.content_id
    ) {
      const producers = nodes.filter(
        (node) =>
          node.toolCallId &&
          "tool_call_id" in step &&
          node.toolCallId === step.tool_call_id,
      );
      const producer =
        step.kind === "table"
          ? (producers.find((node) => node.detailKind === "sql") ??
            producers[0])
          : (producers.find((node) => node.detailKind === "chart") ??
            producers[0]);
      contentNode.set(step.content_id, producer?.id ?? rootId);
    }
  }
  return {
    id: assistant.message_id,
    index,
    question: user?.content ?? "",
    assistant,
    transcript,
    nodes,
    durationMs,
    contentNode,
  };
}

function toAgentMessage(turn: ThreadTurn): AgentMessage {
  return {
    message_id: turn.message_id,
    role:
      turn.role === "tool"
        ? "tool"
        : turn.role === "assistant"
          ? "assistant"
          : "user",
    content: turn.content,
    created_at: turn.created_at,
    steps: turn.steps,
    prompt_tokens: turn.prompt_tokens,
    completion_tokens: turn.completion_tokens,
    total_tokens: turn.total_tokens,
    model_name: turn.model_name,
  };
}

function emptyTranscript(message: ThreadTurn): TranscriptTurn {
  return {
    id: message.message_id,
    question: "",
    steps: [],
    answer: message.content,
    content: [],
    pendingConsent: null,
    blocks: { tables: [], charts: [], citations: [] },
    state: "done",
  };
}

function responseNodeFor(group: TraceGroup): TraceNode | undefined {
  return [...group.nodes]
    .reverse()
    .find((node) => node.detailKind === "response");
}

function nodeIcon(node: TraceNode) {
  if (node.depth === 0) return Bot;
  if (node.status === "failed") return CircleAlert;
  if (node.detailKind === "semantic") return FileJson;
  if (node.detailKind === "sql" || node.detailKind === "tool") return Database;
  if (node.detailKind === "chart") return BarChart3;
  if (node.detailKind === "response") return MessageSquareText;
  if (node.detailKind === "provider") return Sparkles;
  if (node.detailKind === "context") return Clock3;
  return Route;
}

function stepId(step: TraceStep): string | undefined {
  return "step_id" in step ? step.step_id : undefined;
}
function stepTiming(
  step: TraceStep,
): Pick<TraceNode, "startedOffsetMs" | "durationMs"> {
  return "started_offset_ms" in step
    ? { startedOffsetMs: step.started_offset_ms, durationMs: step.duration_ms }
    : {};
}
function isFailedStep(step: TraceStep): boolean {
  return "status" in step && step.status === "failed";
}
function normalizeStatus(status: string): TraceNode["status"] {
  if (["failed", "denied", "cancelled"].includes(status)) return "failed";
  return ["running", "pending"].includes(status) ? "running" : "done";
}
function reasoningLabel(phase: string): string {
  if (phase === "observe") return "Result Review";
  if (phase === "skill") return "Skill Loading";
  if (phase === "answer") return "LLM Response Generation";
  return "LLM Planning";
}
function toolLabel(name: string): string {
  if (name === "semantic_query") return "Semantic Query";
  if (name === "query_execute") return "SQL Execution";
  if (name === "data_to_chart") return "Chart Generation";
  if (name === "load_skill") return "Skill Loading";
  return name
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character: string) => character.toUpperCase());
}
function toolTraceDetail(step?: TraceStep): Record<string, unknown> | null {
  return step?.kind === "tool" && step.trace_detail ? step.trace_detail : null;
}
function objectValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function arrayOfObjects(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value
        .map(objectValue)
        .filter((item): item is Record<string, unknown> => Boolean(item))
    : [];
}
function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}
function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function formatDuration(milliseconds: number): string {
  if (milliseconds < 1000) return `${Math.max(0, Math.round(milliseconds))}ms`;
  if (milliseconds < 60_000)
    return `${(milliseconds / 1000).toFixed(milliseconds < 10_000 ? 1 : 0)}s`;
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = Math.round((milliseconds % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}
function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
