import { FileCode } from "lucide-react";
import { cn } from "@/lib/utils";
import { ActivityTrace } from "./activity-trace";
import { Markdown } from "./markdown";
import type { AttachedQuery } from "./query-attach";
import type { TurnContext } from "./stream-client";
import type { NoveEventInput } from "./app-context";
import { ToolCallCard, type ToolCallCardProps } from "./tool-call-card";
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
  onExecutionEvent?: (event: NoveEventInput) => void;
  onFixWithNove?: (prompt: string) => void;
};

export function MessageList({
  messages,
  onDecide,
  decidingToolCallId,
  statusMessage,
  activeContext,
  onExecutionEvent,
  onFixWithNove,
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
          onFixWithNove={onFixWithNove}
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
  onFixWithNove,
}: {
  message: TranscriptMessage;
  onDecide?: ToolCallCardProps["onDecide"];
  busy?: boolean;
  runContext?: TurnContext;
  onExecutionEvent?: (event: NoveEventInput) => void;
  onFixWithNove?: (prompt: string) => void;
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
          <Markdown runContext={runContext} onExecutionEvent={onExecutionEvent} onFixWithNove={onFixWithNove}>{message.content}</Markdown>
        )}
        {message.turn_state === "streaming" && !isUser ? (
          <span className="mt-1 inline-block h-3 w-1.5 animate-pulse bg-foreground/60 align-middle" />
        ) : null}
        {marker ? (
          <span className="mt-1 block text-xs text-muted-foreground italic">
            {marker}
          </span>
        ) : null}
      </div>
    </div>
  );
}
