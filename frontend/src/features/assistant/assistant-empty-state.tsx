import { MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ThreadView } from "./thread-client";
import type { NoveSuggestedAction } from "./surface-registry";

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
  surfaceTitle?: string | null;
  suggestedActions?: readonly NoveSuggestedAction[];
};

export const ASSISTANT_TOUR_PROMPT = "What can you help me do here?";

const MAX_RECENT_TITLE_LENGTH = 36;

function displayTitle(title: string): string {
  const value = title.trim() || "New conversation";
  const characters = Array.from(value);
  if (characters.length <= MAX_RECENT_TITLE_LENGTH) return value;
  return `${characters.slice(0, MAX_RECENT_TITLE_LENGTH).join("").trimEnd()}…`;
}

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

export function AssistantEmptyState({
  userName,
  onSendMessage,
  onOpenThread,
  recentThreads,
  activeThreadId,
  surfaceTitle,
  suggestedActions,
}: AssistantEmptyStateProps) {
  const recent = recentThreads ?? [];
  const name = userName?.trim() || "there";
  const canStart = Boolean(onSendMessage);
  const actions = suggestedActions?.slice(0, 4) ?? [];

  return (
    <div className="flex min-h-full min-w-0 w-full flex-col items-start justify-start px-4 pt-10 pb-2">
      <h2 className="text-2xl leading-tight font-semibold tracking-tight text-foreground">
        Hi {name},
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        {surfaceTitle && actions.length
          ? `Working in ${surfaceTitle}`
          : "How can I help?"}
      </p>

      {canStart && actions.length ? (
        <div className="mt-8 w-full min-w-0">
          <p className="mb-1 text-xs font-medium text-muted-foreground">
            Ask Nove
          </p>
          {actions.map((action) => (
            <button
              key={action.label}
              type="button"
              onClick={() => onSendMessage?.(action.prompt)}
              className="block min-h-11 w-full border-b border-border/60 px-1 py-2 text-left text-sm hover:underline hover:underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {action.label}
            </button>
          ))}
        </div>
      ) : canStart ? (
        <button
          type="button"
          onClick={() => onSendMessage?.(ASSISTANT_TOUR_PROMPT)}
          className={cn(
            "mt-8 inline-flex min-h-11 items-center rounded-sm px-1 text-sm font-medium text-foreground",
            "hover:underline hover:underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          {ASSISTANT_TOUR_PROMPT}
        </button>
      ) : null}

      {recent.length > 0 ? (
        <div className="mt-10 min-w-0 w-full">
          <p className="px-1 text-sm text-muted-foreground">Recent chats</p>
          <ul className="mt-2 flex min-w-0 w-full flex-col">
            {recent.map((thread) => (
              <li key={thread.thread_id} className="min-w-0 w-full">
                <button
                  type="button"
                  title={thread.title?.trim() || "New conversation"}
                  disabled={!onOpenThread}
                  onClick={() => void onOpenThread?.(thread.thread_id)}
                  className={cn(
                    "flex min-w-0 w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm",
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
                    {displayTitle(thread.title)}
                  </span>
                  <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">
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
