import { useState } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { History, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { listThreads, type ThreadView } from "./thread-client";

export type ThreadHistoryProps = {
  activeThreadId: string | null;
  onOpenThread: (threadId: string) => void | Promise<void>;
  disabled?: boolean;
};

function formatWhen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Conversation history behind a header button. Opening the popover refetches the
 * list so a thread created moments ago appears; selecting one switches the
 * panel to it and closes the popover.
 */
export function ThreadHistory({
  activeThreadId,
  onOpenThread,
  disabled,
}: ThreadHistoryProps) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();

  const { data, isFetching, isError, refetch, hasNextPage, fetchNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["assistant-threads"],
    queryFn: ({ pageParam }) => pageParam ? listThreads(pageParam) : listThreads(),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: open,
    staleTime: 0,
  });

  const threads = [...new Map(data?.pages.flatMap((page) => page.threads).map((thread) => [thread.thread_id, thread]) ?? []).values()];

  const handleSelect = (thread: ThreadView) => {
    setOpen(false);
    void onOpenThread(thread.thread_id);
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next)
          void queryClient.invalidateQueries({
            queryKey: ["assistant-threads"],
          });
      }}
    >
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={disabled}
          aria-label="Chat history"
        >
          <History aria-hidden="true" className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-0">
        <div className="border-b px-3 py-2">
          <p className="text-sm font-medium">Chat history</p>
        </div>
        <ScrollArea className="h-80">
          {threads.length === 0 ? (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">
              {isFetching ? "Loading…" : isError ? "History is unavailable." : hasNextPage ? "More conversations are available." : "No conversations yet."}
            </p>
          ) : (
            <ul className="flex flex-col p-1">
              {threads.map((thread) => (
                <li key={thread.thread_id}>
                  <button
                    type="button"
                    onClick={() => handleSelect(thread)}
                    className={cn(
                      "flex w-full flex-col items-start gap-1 rounded-sm px-2 py-2 text-left text-sm",
                      "hover:bg-accent hover:text-accent-foreground",
                      thread.thread_id === activeThreadId &&
                        "bg-accent text-accent-foreground",
                    )}
                  >
                    <span className="flex w-full items-center gap-2">
                      <MessageSquare
                        aria-hidden="true"
                        className="size-3.5 shrink-0 text-muted-foreground"
                      />
                      <span className="truncate">
                        {thread.title || "New conversation"}
                      </span>
                    </span>
                    <span className="pl-5 text-xs text-muted-foreground">
                      {formatWhen(thread.updated_at)}
                      {thread.message_count > 0
                        ? ` · ${thread.message_count} messages`
                        : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </ScrollArea>
        {isError || hasNextPage ? (
          <div className="border-t p-2">
            <Button type="button" variant="ghost" size="sm" className="h-auto w-full py-3 sm:py-2" disabled={isFetching}
              onClick={() => void (isError ? refetch() : fetchNextPage())}>
              {isFetchingNextPage ? "Loading…" : isError ? "Retry history" : "Load more conversations"}
            </Button>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
