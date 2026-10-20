import { Markdown } from "@/features/assistant/markdown";
import type { TurnContext } from "@/features/assistant/stream-client";
import { cn } from "@/lib/utils";
import { CitationList, ResultChart, ResultTable } from "./result-cards";
import { TextShimmer } from "./text-shimmer";
import type { OrderedContent, TranscriptTurn } from "./studio-chat";
import type { SavableContent } from "./artifact-source";
import { visibleContent } from "./turn-content-order";

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
}: {
  turn: TranscriptTurn;
  runContext?: TurnContext;
  running?: boolean;
  selectedContentId?: string | null;
  onSelectContent?: (item: OrderedContent | null) => void;
  onSaveArtifact?: (item: SavableContent) => void;
  savingContentId?: string | null;
  savedContentIds?: ReadonlySet<string>;
}) {
  const ordered = visibleContent(turn.content);
  const representedTables = ordered.flatMap((item) => item.type === "table" ? [item.block] : []);

  if (!ordered.length && !turn.answer) return null;

  if (!ordered.length) {
    return (
      <SelectableOutput
        contentId="answer"
        selected={selectedContentId === "answer"}
        onSelect={onSelectContent ? () => onSelectContent(null) : undefined}
      >
        <div className="nova-chat-item min-w-0 text-sm">
          <Markdown runContext={runContext}>{turn.answer}</Markdown>
        </div>
      </SelectableOutput>
    );
  }

  return (
    <div className="flex flex-col gap-3" data-testid="ordered-response">
      {ordered.map((item, itemIndex) => (
        <SelectableOutput
          key={item.id}
          contentId={item.id}
          selected={selectedContentId === item.id}
          onSelect={onSelectContent ? () => onSelectContent(item) : undefined}
        >
          {item.type === "text" ? (
            <div className="nova-chat-item min-w-0 text-sm">
              <Markdown runContext={runContext} representedTables={representedTables}>{item.text}</Markdown>
              {running && !item.complete && itemIndex === ordered.length - 1 ? (
                <TextShimmer className="mt-1">Writing</TextShimmer>
              ) : null}
            </div>
          ) : item.type === "table" ? (
            <ResultTable
              block={item.block}
              onSave={onSaveArtifact ? () => onSaveArtifact(item) : undefined}
              saving={savingContentId === item.id}
              saved={savedContentIds?.has(item.id)}
            />
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
