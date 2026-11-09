import { FileCode, FileText } from "lucide-react";
import { CitationList, ResultChart } from "@/features/studio/result-cards";
import { ResultTable } from "@/features/studio/result-table";
import { cn } from "@/lib/utils";
import { ActivityTrace } from "./activity-trace";
import { Markdown } from "./markdown";
import type { AttachedQuery } from "./query-attach";
import type { TurnContext } from "./stream-client";
import type { NevaEventInput } from "./app-context";
import { ToolCallCard, type ToolCallCardProps } from "./tool-call-card";
import { IntelligentUi } from "@/features/studio/intelligent-ui/renderer";
import type { UiAction } from "@/features/studio/intelligent-ui/types";
import type { TranscriptMessage } from "./use-assistant-transcript";

export type MessageListProps = {
  messages: TranscriptMessage[];
  onDecide?: ToolCallCardProps["onDecide"];
  /** Set while a decision request is in flight for the pending tool call. */
  decidingToolCallId?: string | null;
  /** Announced to assistive tech on state transitions, never per token. */
  statusMessage?: string | null;
  /** Context a SQL code card's Run button executes against. */
  activeContext?: TurnContext;
  onExecutionEvent?: (event: NevaEventInput) => void;
  onFixWithNeva?: (prompt: string) => void;
  /** The conversation interactive answers belong to, and how their actions are sent. */
  uiContext?: UiContext;
};

export type UiContext = {
  threadId: string;
  onAction: (message: string, action: UiAction) => Promise<void>;
};

export function MessageList({
  messages,
  onDecide,
  decidingToolCallId,
  statusMessage,
  activeContext,
  onExecutionEvent,
  onFixWithNeva,
  uiContext,
}: MessageListProps) {
  return (
    <div className="flex flex-col gap-3">
      <p role="status" aria-live="polite" className="sr-only">
        {statusMessage ?? ""}
      </p>
      {messages.map((message) => (
        <MessageBubble
          key={message.message_id}
          message={message}
          onDecide={onDecide}
          busy={decidingToolCallId === message.tool_call_id}
          runContext={activeContext}
          onExecutionEvent={onExecutionEvent}
          onFixWithNeva={onFixWithNeva}
          uiContext={uiContext}
        />
      ))}
    </div>
  );
}

const TURN_MARKER: Record<
  NonNullable<TranscriptMessage["turn_state"]>,
  string | null
> = {
  streaming: null,
  done: null,
  cancelled: "Stopped before the answer finished.",
  error: null,
};

/**
 * A sent attachment, shown as a read-only card inside the user bubble rather
 * than as the serialized prompt the model received. The wire prompt keeps the
 * full preamble; this is the display form only.
 */
function SentAttachmentCard({ attachment }: { attachment: AttachedQuery }) {
  const meta = [attachment.database, attachment.schema]
    .filter(Boolean)
    .join(" · ");
  return (
    <span
      className="flex max-w-full items-center gap-1.5 rounded-md border border-primary-foreground/25 bg-primary-foreground/10 px-2 py-1 text-xs"
      title={attachment.sql}
    >
      <FileCode aria-hidden="true" className="size-3 shrink-0 opacity-80" />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">
          {attachment.fileName || "Selection"}
        </span>
        {meta ? (
          <span className="block truncate text-[11px] opacity-75">{meta}</span>
        ) : null}
      </span>
      <span className="shrink-0 text-[11px] opacity-75">
        {attachment.endLine - attachment.startLine + 1} ln
      </span>
    </span>
  );
}

function MessageBubble({
  message,
  onDecide,
  busy,
  runContext,
  onExecutionEvent,
  onFixWithNeva,
  uiContext,
}: {
  message: TranscriptMessage;
  onDecide?: ToolCallCardProps["onDecide"];
  busy?: boolean;
  runContext?: TurnContext;
  onExecutionEvent?: (event: NevaEventInput) => void;
  onFixWithNeva?: (prompt: string) => void;
  uiContext?: UiContext;
}) {
  if (message.role === "activity") {
    return <ActivityTrace message={message} />;
  }

  if (message.role === "tool" && message.tool_call) {
    return (
      <ToolCallCard
        toolCall={message.tool_call}
        toolCallId={message.tool_call_id}
        onDecide={onDecide}
        busy={busy}
      />
    );
  }

  const isUser = message.role === "user";
  const marker = message.turn_state ? TURN_MARKER[message.turn_state] : null;
  const attachments = message.attachments ?? [];
  // A restored message has no structured attachments, so fall back to the wire
  // text. A live send shows the cards plus only the text the user typed.
  const text = isUser
    ? (message.display_text ?? message.content)
    : message.content;

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        data-role={message.role}
        className={cn(
          "max-w-[85%] min-w-0 rounded-lg px-3 py-2 text-sm",
          isUser
            ? "bg-primary text-primary-foreground whitespace-pre-wrap"
            : "border bg-surface-2 text-foreground",
          message.turn_state === "error" &&
            "border-destructive/40 text-destructive",
        )}
      >
        {isUser && message.file_names?.length ? (
          <div className="mb-2 flex flex-wrap gap-1.5" aria-label="Attached files">
            {message.file_names.map((name) => (
              <span
                key={name}
                className="inline-flex max-w-full items-center gap-1 rounded-md bg-primary-foreground/15 px-1.5 py-0.5 text-xs"
              >
                <FileText aria-hidden="true" className="size-3 shrink-0" />
                <span className="truncate">{name}</span>
              </span>
            ))}
          </div>
        ) : null}
        {isUser && attachments.length > 0 ? (
          <div
            className="mb-2 flex flex-col gap-1.5"
            aria-label="Attached queries"
          >
            {attachments.map((attachment) => (
              <SentAttachmentCard key={attachment.id} attachment={attachment} />
            ))}
          </div>
        ) : null}
        {/* The model answers in markdown; a user's own text is shown verbatim. */}
        {isUser ? (
          text
        ) : (
          <Markdown runContext={runContext} onExecutionEvent={onExecutionEvent} onFixWithNeva={onFixWithNeva}>{message.content}</Markdown>
        )}
        {!isUser ? <AnswerBlocks message={message} uiContext={uiContext} /> : null}
        {message.turn_state === "streaming" && !isUser ? (
          <span className="mt-1 inline-block h-3 w-1.5 animate-pulse bg-foreground/60 align-middle" />
        ) : null}
        {marker ? (
          <span className="mt-1 block text-xs text-muted-foreground italic">
            {marker}
          </span>
        ) : null}
        {!isUser && message.turn_state !== "streaming" && message.suggestions?.length ? (
          <FollowUps suggestions={message.suggestions} onAsk={onFixWithNeva} />
        ) : null}
      </div>
    </div>
  );
}

/** The tables, charts and sources an answer produced, in the order they came. */
function AnswerBlocks({
  message,
  uiContext,
}: {
  message: TranscriptMessage;
  uiContext?: UiContext;
}) {
  const blocks = message.blocks ?? [];
  if (!blocks.length) return null;
  const citations = blocks.flatMap((item) =>
    item.kind === "citation" ? [item.block] : [],
  );
  // A stored answer has its server id; only then can its blocks keep state.
  const stored = message.turn_state === "done" && !message.message_id.startsWith("assistant-");
  return (
    <div className="mt-2 flex min-w-0 flex-col gap-2">
      {blocks.map((item, index) =>
        item.kind === "table" ? (
          <ResultTable key={`table-${index}`} block={item.block} />
        ) : item.kind === "chart" ? (
          <ResultChart key={`chart-${index}`} block={item.block} />
        ) : item.kind === "ui" ? (
          message.turn_state === "cancelled" ||
          message.turn_state === "error" ||
          (message.turn_state === "done" && !item.complete) ? (
            <Markdown key={item.id} offline>
              {item.block.fallback}
            </Markdown>
          ) : (
            <IntelligentUi
              key={item.id}
              block={item.block}
              complete={item.complete}
              source={
                uiContext && stored
                  ? {
                      threadId: uiContext.threadId,
                      messageId: message.message_id,
                      contentId: item.id,
                    }
                  : undefined
              }
              onAction={uiContext?.onAction}
            />
          )
        ) : null,
      )}
      <CitationList citations={citations} />
    </div>
  );
}

/** Questions Neva suggests next; choosing one asks it. */
function FollowUps({
  suggestions,
  onAsk,
}: {
  suggestions: string[];
  onAsk?: (prompt: string) => void;
}) {
  if (!onAsk) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Suggested follow-ups">
      {suggestions.map((suggestion) => (
        <button
          key={suggestion}
          type="button"
          onClick={() => onAsk(suggestion)}
          className="rounded-full border bg-background px-2.5 py-1 text-left text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          {suggestion}
        </button>
      ))}
    </div>
  );
}
