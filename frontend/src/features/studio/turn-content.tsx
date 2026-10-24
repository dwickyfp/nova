import { SmartReport } from "@/features/smart-reports/renderer";
import { DashboardArtifactCard } from "@/features/viz/dashboard-artifact-card";
import { Markdown } from "@/features/assistant/markdown";
import type { TurnContext } from "@/features/assistant/stream-client";
import { cn } from "@/lib/utils";
import { CitationList, ResultChart } from "./result-cards";
import { ResultTable } from "./result-table";
import { TextShimmer } from "./text-shimmer";
import type { OrderedContent, TranscriptTurn } from "./studio-chat";
import { uiNodeContentId, type SavableContent } from "./artifact-source";
import { visibleContent } from "./turn-content-order";
import { IntelligentUi } from "./intelligent-ui/renderer";
import type { UiAction, UiArtifactRequest } from "./intelligent-ui/types";
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
  onSaveArtifact,
  savingContentId,
  savedContentIds,
  uiContext,
}: {
  turn: TranscriptTurn;
  runContext?: TurnContext;
  running?: boolean;
  selectedContentId?: string | null;
  onSelectContent?: (item: OrderedContent | null) => void;
  onSaveArtifact?: (item: SavableContent) => void;
  savingContentId?: string | null;
  savedContentIds?: ReadonlySet<string>;
  uiContext?: {
    agentId: string;
    threadId: string;
    onAction: (message: string, action: UiAction) => Promise<void>;
  };
}) {
  const ordered = visibleContent(turn.content);
  const representedTables = ordered.flatMap((item) =>
    item.type === "table" ? [item.block] : [],
  );
  const uiArtifact = (contentId: string) => ({
    onSaveArtifact: onSaveArtifact
      ? (request: UiArtifactRequest) =>
          onSaveArtifact({
            id: uiNodeContentId(contentId, request),
            type: "ui-node",
            request,
            block: { tool_call_id: request.toolCallId },
          })
      : undefined,
    artifactStatus: (request: UiArtifactRequest) => {
      const id = uiNodeContentId(contentId, request);
      return savingContentId === id ? "saving" as const
        : savedContentIds?.has(id) ? "saved" as const : "idle" as const;
    },
  });
  const sourceTables = ordered.filter((item) => item.type === "table");
  const foldSources = ordered.some(
    (item) => item.type === "ui" && item.complete &&
      item.block.catalog_version === UI_CATALOG_VERSION,
  ) && turn.state === "done";
  const renderTable = (item: Extract<OrderedContent, { type: "table" }>) => (
    <ResultTable
      block={item.block}
      onSave={onSaveArtifact ? () => onSaveArtifact(item) : undefined}
      saving={savingContentId === item.id}
      saved={savedContentIds?.has(item.id)}
    />
  );

  if (!ordered.length && !turn.answer) return null;

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
      {ordered.map((item, itemIndex) => foldSources && item.type === "table" ? (
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
            onSelectContent && item.type !== "ui" && item.type !== "smart_report"
              ? () => onSelectContent(item)
              : undefined
          }
        >
          {item.type === "text" ? (
            <div className="nova-chat-item min-w-0 max-w-3xl text-sm">
              <Markdown
                offline
                runContext={runContext}
                representedTables={representedTables}
              >
                {item.text}
              </Markdown>
              {running && !item.complete && itemIndex === ordered.length - 1 ? (
                <TextShimmer className="mt-1">Writing</TextShimmer>
              ) : null}
            </div>
          ) : item.type === "dashboard" ? (
            <DashboardArtifactCard block={item.block} />
          ) : item.type === "smart_report" ? (
            <SmartReport block={item.block} origin={uiContext ? { agent_id: uiContext.agentId, thread_id: uiContext.threadId, message_id: turn.messageId, content_id: item.id } : undefined} />
          ) : item.type === "ui" ? (
            turn.state === "cancelled" ||
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
                {...uiArtifact(item.id)}
              />
            )
          ) : item.type === "table" ? (
            renderTable(item)
          ) : item.type === "chart" ? (
            <ResultChart
              block={item.block}
              onSave={onSaveArtifact ? () => onSaveArtifact(item) : undefined}
              saving={savingContentId === item.id}
              saved={savedContentIds?.has(item.id)}
            />
          ) : (
            <CitationList citations={[item.block]} />
          )}
        </SelectableOutput>
      ))}
    </div>
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
