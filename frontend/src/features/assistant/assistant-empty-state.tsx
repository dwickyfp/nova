import { Sparkles, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ThreadView } from "./thread-client";

export type AssistantEmptyStateProps = {
  /** First name (or username) the greeting addresses; null falls back to "there". */
  userName?: string | null;
  /** Sends the starter prompt that opens the tour. */
  onSendMessage?: (message: string) => void;
  /** Switches the conversation to an existing thread. */
  onOpenThread?: (threadId: string) => void | Promise<void>;
  /**
   * Recent conversations to list. Empty or absent hides the section; the panel
   * supplies these from its provider so a bare panel never reaches for data it
   * cannot fetch.
   */
  recentThreads?: ThreadView[];
  /** The thread currently open, highlighted in the list. */
  activeThreadId?: string | null;
};

/** The prompt the CTA sends; also the tour's own subject. */
export const ASSISTANT_TOUR_PROMPT = "Show me what Nove can do";

function formatWhen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const diffMs = Date.now() - date.getTime();
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * The pre-conversation surface: a large greeting, a single starter action, and
 * the most recent threads. It replaces the generic empty state so a fresh chat
 * reads as an invitation rather than a void. Recent threads come from the same
 * list the header's history popover uses, so the two never disagree.
 */
export function AssistantEmptyState({
  userName,
  onSendMessage,
  onOpenThread,
  recentThreads,
  activeThreadId,
}: AssistantEmptyStateProps) {
  const recent = recentThreads ?? [];
  const name = userName?.trim() || "there";
  const canStart = Boolean(onSendMessage);

  return (
    <div className="flex min-h-full flex-col items-start justify-start px-4 pt-10 pb-2">
      <h2 className="text-4xl leading-tight font-semibold tracking-tight text-foreground">
        Hi {name},
      </h2>
      <p className="mt-1 text-4xl leading-tight font-semibold tracking-tight text-muted-foreground">
        How can I help?
      </p>

      {canStart ? (
        <button
          type="button"
          onClick={() => onSendMessage?.(ASSISTANT_TOUR_PROMPT)}
          className={cn(
            "mt-8 inline-flex items-center gap-2 rounded-full border border-primary/50 px-4 py-2 text-sm font-medium text-primary",
            "transition-colors hover:bg-primary/10 focus-visible:ring-ring/50 focus-visible:ring-[3px] focus-visible:outline-none",
          )}
        >
          <Sparkles aria-hidden="true" className="size-4" />
          {ASSISTANT_TOUR_PROMPT}
        </button>
      ) : null}

      {recent.length > 0 ? (
        <div className="mt-10 w-full">
          <p className="px-1 text-sm text-muted-foreground">Recent chats</p>
          <ul className="mt-2 flex flex-col">
            {recent.map((thread) => (
              <li key={thread.thread_id}>
                <button
                  type="button"
                  disabled={!onOpenThread}
                  onClick={() => void onOpenThread?.(thread.thread_id)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm",
                    "hover:bg-accent hover:text-accent-foreground disabled:cursor-default",
                    thread.thread_id === activeThreadId &&
                      "bg-accent text-accent-foreground",
                  )}
                >
                  <MessageSquare
                    aria-hidden="true"
                    className="size-4 shrink-0 text-muted-foreground"
                  />
                  <span className="min-w-0 flex-1 truncate">
                    {thread.title || "New conversation"}
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatWhen(thread.updated_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
