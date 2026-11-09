import { useEffect, useState } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { Check, History, MessageSquare, MoreHorizontal, Pencil, Search, Trash2, X } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import {
  deleteThread,
  listThreads,
  renameThread,
  searchThreads,
  type ThreadView,
} from "./thread-client";

export type ThreadHistoryProps = {
  activeThreadId: string | null;
  onOpenThread: (threadId: string) => void | Promise<void>;
  /** Called after a conversation is deleted, so an open one can be closed. */
  onDeletedThread?: (threadId: string) => void;
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
 * Conversation history behind a header button: search by title, open, rename
 * or delete a conversation. Opening the popover refetches the list so a thread
 * created moments ago appears.
 */
export function ThreadHistory({
  activeThreadId,
  onOpenThread,
  onDeletedThread,
  disabled,
}: ThreadHistoryProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [deleting, setDeleting] = useState<ThreadView | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    const timer = window.setTimeout(() => setTerm(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const { data, isFetching, isError, refetch, hasNextPage, fetchNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["assistant-threads", term],
    queryFn: ({ pageParam }) =>
      term ? searchThreads(term) : pageParam ? listThreads(pageParam) : listThreads(),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: open,
    staleTime: 0,
  });

  const threads = [...new Map(data?.pages.flatMap((page) => page.threads).map((thread) => [thread.thread_id, thread]) ?? []).values()];

  const refresh = () =>
    void queryClient.invalidateQueries({ queryKey: ["assistant-threads"] });

  const handleSelect = (thread: ThreadView) => {
    setOpen(false);
    void onOpenThread(thread.thread_id);
  };

  const saveTitle = async () => {
    if (!renaming) return;
    const title = renaming.title.trim();
    if (!title) return;
    setFailure(null);
    try {
      await renameThread(renaming.id, title);
      setRenaming(null);
      refresh();
    } catch {
      setFailure("The conversation could not be renamed. Try again.");
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    const removed = deleting.thread_id;
    setFailure(null);
    try {
      await deleteThread(removed);
      setDeleting(null);
      refresh();
      onDeletedThread?.(removed);
    } catch {
      setDeleting(null);
      setFailure("The conversation could not be deleted. Try again.");
    }
  };

  return (
    <>
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (next) refresh();
          else setRenaming(null);
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
        <PopoverContent align="end" className="w-80 p-0">
          <div className="flex flex-col gap-2 border-b p-2">
            <p className="px-1 text-sm font-medium">Chat history</p>
            <div className="relative">
              <Search
                aria-hidden="true"
                className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search conversations"
                aria-label="Search conversations"
                className="h-8 pl-8 text-sm"
              />
            </div>
          </div>
          {failure ? (
            <p role="alert" className="border-b px-3 py-2 text-xs text-destructive">
              {failure}
            </p>
          ) : null}
          <ScrollArea className="h-80">
            {threads.length === 0 ? (
              <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                {isFetching
                  ? "Loading…"
                  : isError
                    ? "History is unavailable."
                    : term
                      ? "No conversation matches this search."
                      : hasNextPage
                        ? "More conversations are available."
                        : "No conversations yet."}
              </p>
            ) : (
              <ul className="flex flex-col p-1">
                {threads.map((thread) => (
                  <li
                    key={thread.thread_id}
                    className={cn(
                      "group flex items-start gap-1 rounded-sm",
                      "hover:bg-accent hover:text-accent-foreground",
                      thread.thread_id === activeThreadId && "bg-accent text-accent-foreground",
                    )}
                  >
                    {renaming?.id === thread.thread_id ? (
                      <form
                        className="flex w-full items-center gap-1 p-1"
                        onSubmit={(event) => {
                          event.preventDefault();
                          void saveTitle();
                        }}
                      >
                        <Input
                          autoFocus
                          value={renaming.title}
                          maxLength={80}
                          aria-label="Conversation name"
                          onChange={(event) =>
                            setRenaming({ id: thread.thread_id, title: event.target.value })
                          }
                          onKeyDown={(event) => {
                            if (event.key === "Escape") setRenaming(null);
                          }}
                          className="h-8 text-sm"
                        />
                        <Button type="submit" variant="ghost" size="icon" className="size-8" aria-label="Save name">
                          <Check aria-hidden="true" className="size-4" />
                        </Button>
                        <Button type="button" variant="ghost" size="icon" className="size-8" aria-label="Cancel renaming"
                          onClick={() => setRenaming(null)}>
                          <X aria-hidden="true" className="size-4" />
                        </Button>
                      </form>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => handleSelect(thread)}
                          className="flex min-w-0 flex-1 flex-col items-start gap-1 rounded-sm px-2 py-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <span className="flex w-full min-w-0 items-center gap-2">
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
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="mt-1 mr-1 size-8 shrink-0 text-muted-foreground"
                              aria-label={`Actions for ${thread.title || "this conversation"}`}
                            >
                              <MoreHorizontal aria-hidden="true" className="size-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              onSelect={() =>
                                setRenaming({ id: thread.thread_id, title: thread.title || "" })
                              }
                            >
                              <Pencil aria-hidden="true" className="size-4" />
                              Rename
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              variant="destructive"
                              onSelect={() => setDeleting(thread)}
                            >
                              <Trash2 aria-hidden="true" className="size-4" />
                              Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </ScrollArea>
          {isError || (hasNextPage && !term) ? (
            <div className="border-t p-2">
              <Button type="button" variant="ghost" size="sm" className="h-auto w-full py-3 sm:py-2" disabled={isFetching}
                onClick={() => void (isError ? refetch() : fetchNextPage())}>
                {isFetchingNextPage ? "Loading…" : isError ? "Retry history" : "Load more conversations"}
              </Button>
            </div>
          ) : null}
        </PopoverContent>
      </Popover>
      <AlertDialog open={Boolean(deleting)} onOpenChange={(next) => !next && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              “{deleting?.title || "New conversation"}” and its answers are removed for good.
              This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={(event) => {
                event.preventDefault();
                void confirmDelete();
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
