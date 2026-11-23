import { useMemo } from "react";
import { ShieldCheck } from "lucide-react";
import { DashboardArtifactCard } from "@/features/viz/dashboard-artifact-card";
import { Markdown } from "@/features/assistant/markdown";
import type { TurnContext } from "@/features/assistant/stream-client";
import type { ChartBlock } from "@/features/assistant/types";
import { cn } from "@/lib/utils";
import { CitationList, ResultChart } from "./result-cards";
import { ResultTable } from "./result-table";
import { TextShimmer } from "./text-shimmer";
import { StreamingMarkdown } from "./streaming-markdown";
import type { OrderedContent, TranscriptTurn } from "./studio-chat";
import { AddToDatavizButton } from "./dataviz/add-to-dataviz";
import type { DatavizLocator } from "./dataviz/api";
import { visibleContent } from "./turn-content-order";
import { IntelligentUi } from "./intelligent-ui/renderer";
import type { UiAction } from "./intelligent-ui/types";
import { UI_CATALOG_VERSION } from "./intelligent-ui/catalog";

/**
 * The canonical renderer for an agent turn's authored output.
 *
 * Studio and Observability both use this component. Keeping the ordered block
 * renderer in one place prevents a reopened trace from degrading into plain
 * Markdown and silently losing tables, charts, or citations.
 */
export function TurnContent({
  turn,
  runContext,
  running = false,
  selectedContentId,
  onSelectContent,
  datavizThreadId,
  uiContext,
  content,
}: {
  turn: TranscriptTurn;
  /**
   * The content to show, already in stream order. Studio passes one segment
   * of the turn at a time; without it the whole turn's content is shown.
   */
  content?: OrderedContent[];
  runContext?: TurnContext;
  running?: boolean;
  selectedContentId?: string | null;
  onSelectContent?: (item: OrderedContent | null) => void;
  /**
   * The stored thread this turn belongs to. Its results then offer
   * **Add to Dataviz** when Nova's server allows it; nothing otherwise.
   */
  datavizThreadId?: string | null;
  uiContext?: {
    agentId: string;
    threadId: string;
    onAction: (message: string, action: UiAction) => Promise<void>;
  };
}) {
  // One request, one visual: a tool chart of a result an interactive answer
  // already draws is left out, and a table is folded under the chart drawn
  // from it ("Source data") instead of repeating its numbers above it.
  // Studio passes one content segment at a time; a run composes its content
  // at the end, so a table and the chart drawn from it share a segment.
  const all = content ?? visibleContent(turn.content);
  const uiCalls = new Set(
    all.flatMap((item) =>
      item.type === "ui" && item.complete
        ? Object.values(item.block.provenance.tool_calls ?? {})
        : [],
    ),
  );
  // A chart tool's block names its own call; `source_tool_call_id` names the
  // query whose result it drew.
  const drawnFrom = (block: ChartBlock) =>
    block.source_tool_call_id ? [block.tool_call_id, block.source_tool_call_id] : [block.tool_call_id];
  const ordered = all.filter(
    (item) => !(item.type === "chart" && drawnFrom(item.block).some((id) => uiCalls.has(id))),
  );
  const chartedCalls = new Set([
    ...uiCalls,
    ...ordered.flatMap((item) => (item.type === "chart" ? drawnFrom(item.block) : [])),
  ]);
  // Stable while the tables are unchanged, so finished Markdown blocks stay memoised.
  const tableKey = ordered.flatMap((item) => (item.type === "table" ? [item.id] : [])).join("|");
  const representedTables = useMemo(() => ordered.flatMap((item) => (item.type === "table" ? [item.block] : [])), [tableKey]);
  const locate = (contentId: string): DatavizLocator | null =>
    datavizThreadId && turn.messageId && turn.state === "done"
      ? { thread_id: datavizThreadId, message_id: turn.messageId, content_id: contentId }
      : null;
  const foldAll = ordered.some(
    (item) => item.type === "ui" && item.complete &&
      item.block.catalog_version === UI_CATALOG_VERSION,
  );
  const folded = (item: OrderedContent): boolean =>
    turn.state === "done" && item.type === "table" &&
    (foldAll || (!!item.block.tool_call_id && chartedCalls.has(item.block.tool_call_id)));
  const sourceTables = ordered.flatMap((item) =>
    item.type === "table" && folded(item) ? [item] : [],
  );
  const renderTable = (item: Extract<OrderedContent, { type: "table" }>) => (
    <ResultTable
      block={item.block}
      action={<AddToDatavizButton locator={locate(item.id)} />}
    />
  );

  if (!ordered.length && (content || !turn.answer)) return null;

  if (!ordered.length) {
    return (
      <SelectableOutput
        contentId="answer"
        selected={selectedContentId === "answer"}
        onSelect={onSelectContent ? () => onSelectContent(null) : undefined}
      >
        <div className="nova-chat-item min-w-0 max-w-3xl text-sm">
          <Markdown offline runContext={runContext}>
            {turn.answer}
          </Markdown>
        </div>
      </SelectableOutput>
    );
  }

  return (
    <div className="flex flex-col gap-3" data-testid="ordered-response">
      {ordered.map((item, itemIndex) => folded(item) ? (
        item.id === sourceTables[0]?.id ? (
          <details key={item.id} className="min-w-0 text-sm">
            <summary className="w-fit cursor-pointer rounded-sm text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              Source data ({sourceTables.length} {sourceTables.length === 1 ? "table" : "tables"})
            </summary>
            <div className="mt-3 flex flex-col gap-3">
              {sourceTables.map((source) => (
                <SelectableOutput
                  key={source.id}
                  contentId={source.id}
                  selected={selectedContentId === source.id}
                  onSelect={onSelectContent ? () => onSelectContent(source) : undefined}
                >
                  {renderTable(source)}
                </SelectableOutput>
              ))}
            </div>
          </details>
        ) : null
      ) : (
        <SelectableOutput
          key={item.id}
          contentId={item.id}
          selected={selectedContentId === item.id}
          onSelect={
            onSelectContent && item.type !== "ui"
              ? () => onSelectContent(item)
              : undefined
          }
        >
          {item.type === "text" ? (
            <div className="nova-chat-item min-w-0 max-w-3xl text-sm">
              <StreamingMarkdown
                text={item.text}
                animate={running}
                offline
                runContext={runContext}
                representedTables={representedTables}
              />
              {running && !item.complete && itemIndex === ordered.length - 1 ? (
                <TextShimmer className="mt-1">Writing</TextShimmer>
              ) : null}
              {item.verification ? <VerifiedNote state={item.verification} /> : null}
            </div>
          ) : item.type === "dashboard" ? (
            <DashboardArtifactCard block={item.block} />
          ) : item.type === "ui" ? (
            item.tooLarge ? (
              <div className="text-sm">
                <p role="status" className="mb-2 text-muted-foreground">
                  This interactive answer is too large to show. Here is the written answer.
                </p>
                <Markdown offline>{item.block.fallback}</Markdown>
              </div>
            ) : turn.state === "cancelled" ||
            turn.state === "error" ||
            (turn.state === "done" && !item.complete) ? (
              <Markdown offline>{item.block.fallback}</Markdown>
            ) : (
              <IntelligentUi
                block={item.block}
                complete={item.complete}
                source={
                  uiContext && turn.messageId
                    ? {
                        agentId: uiContext.agentId,
                        threadId: uiContext.threadId,
                        messageId: turn.messageId,
                        contentId: item.id,
                      }
                    : undefined
                }
                onAction={uiContext?.onAction}
                dataviz={
                  datavizThreadId && turn.messageId && turn.state === "done"
                    ? { threadId: datavizThreadId, messageId: turn.messageId, contentId: item.id }
                    : undefined
                }
              />
            )
          ) : item.type === "table" ? (
            renderTable(item)
          ) : item.type === "chart" ? (
            <ResultChart block={item.block} dataviz={locate(item.id) ?? undefined} />
          ) : (
            <CitationList citations={[item.block]} />
          )}
        </SelectableOutput>
      ))}
    </div>
  );
}

/** The answer was sent after its figures were checked against the turn's results. */
function VerifiedNote({ state }: { state: "verified" | "adjusted" }) {
  return (
    <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground" data-testid="answer-verified">
      <ShieldCheck aria-hidden="true" className="size-3.5 shrink-0" />
      {state === "verified"
        ? "Figures checked against the results"
        : "Figures checked against the results; unsupported ones were left out"}
    </p>
  );
}

function SelectableOutput({
  children,
  selected,
  onSelect,
  contentId,
}: {
  children: React.ReactNode;
  selected: boolean;
  onSelect?: () => void;
  contentId: string;
}) {
  if (!onSelect) return <>{children}</>;
  return (
    <div
      role="button"
      data-content-id={contentId}
      tabIndex={0}
      aria-pressed={selected}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "rounded-xl outline-none transition-[box-shadow,background-color]",
        "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        selected && "ring-2 ring-ring ring-offset-2 ring-offset-background",
      )}
    >
      {children}
    </div>
  );
}
