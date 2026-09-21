import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  ChevronDown,
  ChevronLeft,
  Coins,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { Markdown } from "@/features/assistant/markdown";
import { observabilityApi, type ThreadTurn } from "@/features/agents/api";

/**
 * Full-screen conversation trace, three panes:
 *
 *   1. the chat, read the way it happened (reuses the Studio renderer);
 *   2. the turn's steps, what the loop did and in what order;
 *   3. the detail for the selected step: user question, the system prompt the
 *      model received, and the output.
 *
 * The step list and the detail pane are linked: selecting a step in pane 2
 * shows its input and output in pane 3. This is the "why did it answer that"
 * view, built from the trace the loop records per turn.
 */
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
  // Index into the flattened turn list; the detail pane follows this.
  const [selected, setSelected] = useState(0);
  // Below `lg` the three panes cannot sit side by side, so one is shown at a
  // time and the header switches between them.
  const [mobilePane, setMobilePane] = useState<"chat" | "steps" | "detail">(
    "chat",
  );

  if (traceQuery.isLoading || !trace) {
    return (
      <FullScreen>
        <Skeleton className="m-6 h-[80vh] w-auto" />
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

  const activeTurn = trace.turns[selected];

  return (
    <FullScreen>
      <header className="flex h-14 shrink-0 items-center justify-between border-b px-5">
        <div className="flex min-w-0 items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label="Close trace"
          >
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
          <span>{trace.user_name}</span>
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

      <div className="flex shrink-0 gap-1 border-b px-4 py-2 lg:hidden">
        {(["chat", "steps", "detail"] as const).map((pane) => (
          <button
            key={pane}
            type="button"
            onClick={() => setMobilePane(pane)}
            className={cn(
              "rounded-full px-3 py-1 text-xs capitalize transition-colors",
              mobilePane === pane
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent",
            )}
          >
            {pane}
          </button>
        ))}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1.2fr)]">
        <div
          className={cn(
            "min-h-0 flex-col lg:flex",
            mobilePane === "chat" ? "flex" : "hidden",
          )}
        >
          <ChatPane turns={trace.turns} />
        </div>
        <div
          className={cn(
            "min-h-0 flex-col lg:flex",
            mobilePane === "steps" ? "flex" : "hidden",
          )}
        >
          <StepsPane
            turns={trace.turns}
            selected={selected}
            onSelect={setSelected}
          />
        </div>
        <div
          className={cn(
            "min-h-0 flex-col lg:flex",
            mobilePane === "detail" ? "flex" : "hidden",
          )}
        >
          <DetailPane turn={activeTurn} />
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

/** Pane 1: the conversation, read top to bottom like a chat. */
function ChatPane({ turns }: { turns: ThreadTurn[] }) {
  return (
    <section className="flex min-h-0 flex-1 flex-col lg:border-r">
      <PaneHeading label="Conversation" />
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-5 px-5 py-6">
          {turns.map((turn) => (
            <div key={turn.message_id}>
              {turn.role === "user" ? (
                <div className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl bg-accent px-4 py-2.5 text-sm text-accent-foreground ring-1 ring-input">
                    <p className="whitespace-pre-wrap break-words">
                      {turn.content}
                    </p>
                  </div>
                </div>
              ) : (
                <div className="text-sm">
                  <Markdown>{turn.content}</Markdown>
                </div>
              )}
            </div>
          ))}
        </div>
      </ScrollArea>
    </section>
  );
}

/** Pane 2: the loop's steps for the whole thread, grouped by turn. */
function StepsPane({
  turns,
  selected,
  onSelect,
}: {
  turns: ThreadTurn[];
  selected: number;
  onSelect: (index: number) => void;
}) {
  return (
    <section className="flex min-h-0 flex-1 flex-col lg:border-r">
      <PaneHeading label="Steps" />
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 px-4 py-4">
          {turns.map((turn, index) => {
            if (turn.role !== "assistant") return null;
            const isActive = index === selected;
            return (
              <button
                key={turn.message_id}
                type="button"
                onClick={() => onSelect(index)}
                className={cn(
                  "w-full rounded-xl border p-3 text-left transition-colors",
                  isActive ? "border-ring bg-muted/60" : "hover:bg-muted/40",
                )}
              >
                <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
                  <span>Turn {index + 1}</span>
                  {turn.total_tokens ? (
                    <span className="tabular-nums">
                      {turn.total_tokens.toLocaleString()} tokens
                    </span>
                  ) : null}
                </div>
                {turn.steps.length === 0 ? (
                  <p className="text-xs text-muted-foreground">Answer only</p>
                ) : (
                  <ol className="space-y-1.5">
                    {turn.steps.map((step, i) => (
                      <li key={i} className="flex items-start gap-2 text-xs">
                        <StepIcon step={step} />
                        <span className="min-w-0">
                          <span className="font-mono">{stepLabel(step)}</span>
                          {"text" in step && step.text ? (
                            <span className="block truncate text-muted-foreground">
                              {step.text}
                            </span>
                          ) : null}
                          {step.kind === "tool" && step.status !== "done" ? (
                            <span className="block text-destructive">
                              {step.status}
                            </span>
                          ) : null}
                        </span>
                      </li>
                    ))}
                  </ol>
                )}
              </button>
            );
          })}
        </div>
      </ScrollArea>
    </section>
  );
}

function StepIcon({ step }: { step: ThreadTurn["steps"][number] }) {
  if (step.kind === "tool")
    return <Wrench className="mt-0.5 size-3 shrink-0" />;
  if (step.kind === "answer")
    return <ArrowUpRight className="mt-0.5 size-3 shrink-0" />;
  if (step.kind === "text")
    return <ArrowUpRight className="mt-0.5 size-3 shrink-0" />;
  if (
    step.kind === "table" ||
    step.kind === "chart" ||
    step.kind === "citation"
  ) {
    return <Wrench className="mt-0.5 size-3 shrink-0" />;
  }
  return <Sparkles className="mt-0.5 size-3 shrink-0" />;
}

function stepLabel(step: ThreadTurn["steps"][number]): string {
  if (step.kind === "tool") return step.name;
  if (step.kind === "answer") return "answer";
  if (step.kind === "text") return "response text";
  if (step.kind === "table") return "table";
  if (step.kind === "chart") return "chart";
  if (step.kind === "citation") return "citations";
  if (step.kind === "context") return "context";
  return step.phase;
}

/** Pane 3: input and output for the selected turn. */
function DetailPane({ turn }: { turn: ThreadTurn | undefined }) {
  if (!turn) {
    return (
      <section className="flex min-h-0 flex-col">
        <PaneHeading label="Detail" />
        <p className="p-4 text-sm text-muted-foreground">Select a turn.</p>
      </section>
    );
  }
  return (
    <section className="flex min-h-0 flex-col">
      <PaneHeading label="Detail" />
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 p-4">
          <Panel title="System instructions" defaultOpen={false}>
            {turn.instructions ? (
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-muted/40 p-3 font-mono text-xs">
                {turn.instructions}
              </pre>
            ) : (
              <p className="text-xs text-muted-foreground">
                Not recorded for this turn.
              </p>
            )}
          </Panel>

          <Panel title="Output">
            {turn.content ? (
              <div className="text-sm">
                <Markdown>{turn.content}</Markdown>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">No text output.</p>
            )}
            {turn.model_name ? (
              <p className="mt-2 font-mono text-xs text-muted-foreground">
                {turn.model_name}
              </p>
            ) : null}
            <div className="mt-3 flex gap-4 border-t pt-2 text-xs text-muted-foreground">
              <span className="tabular-nums">
                prompt {turn.prompt_tokens ?? 0}
              </span>
              <span className="tabular-nums">
                completion {turn.completion_tokens ?? 0}
              </span>
              <span className="tabular-nums">
                total {turn.total_tokens ?? 0}
              </span>
            </div>
          </Panel>

          {turn.steps
            .filter((step) => step.kind === "tool")
            .map((step, i) => (
              <Panel key={i} title={step.kind === "tool" ? step.name : ""}>
                <dl className="space-y-2 text-xs">
                  <div>
                    <dt className="text-muted-foreground">Status</dt>
                    <dd>
                      <Badge
                        variant={
                          step.kind === "tool" && step.status === "done"
                            ? "secondary"
                            : "destructive"
                        }
                      >
                        {step.kind === "tool" ? step.status : ""}
                      </Badge>
                    </dd>
                  </div>
                  {step.kind === "tool" && step.preview ? (
                    <div>
                      <dt className="text-muted-foreground">Preview</dt>
                      <dd className="font-mono">{step.preview}</dd>
                    </div>
                  ) : null}
                  {step.kind === "tool" &&
                  Object.keys(step.arguments).length ? (
                    <div>
                      <dt className="text-muted-foreground">Arguments</dt>
                      <dd className="space-y-1">
                        {Object.entries(step.arguments).map(([key, value]) => (
                          <div key={key} className="flex gap-2">
                            <span className="font-mono text-muted-foreground">
                              {key}
                            </span>
                            <span className="min-w-0 break-words">{value}</span>
                          </div>
                        ))}
                      </dd>
                    </div>
                  ) : null}
                </dl>
              </Panel>
            ))}
        </div>
      </ScrollArea>
    </section>
  );
}

function PaneHeading({ label }: { label: string }) {
  return (
    <div className="flex h-10 shrink-0 items-center border-b px-4 text-xs font-medium text-muted-foreground">
      {label}
    </div>
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
    <Collapsible defaultOpen={defaultOpen} className="rounded-xl border">
      <CollapsibleTrigger className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium">
        {title}
        <ChevronDown className="size-4 text-muted-foreground transition-transform data-[state=open]:rotate-180" />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t px-3 py-3">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}
