import { useEffect, useRef, useState } from "react";
import {
  LayoutGrid,
  LayoutDashboard,
  MessageSquarePlus,
  MoreHorizontal,
  Package,
  PanelLeft,
  Pencil,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { AgentThread } from "@/features/agents/api";
import { relativeUpdatedAt } from "./thread-time";

export type StudioView = "chat" | "artifacts" | "dashboards" | "capabilities";

const NAV: {
  id: Exclude<StudioView, "chat">;
  label: string;
  icon: LucideIcon;
}[] = [
  { id: "artifacts", label: "Artifacts", icon: Package },
  { id: "dashboards", label: "Dashboard", icon: LayoutDashboard },
  { id: "capabilities", label: "Capabilities", icon: LayoutGrid },
];

/**
 * Studio's own left rail. Standalone: it is not Nova's sidebar and shares no
 * styling with it, so Studio reads as its own product.
 *
 * The rail collapses to an icon strip from the header control, the same gesture
 * Nova's main sidebar uses, so Studio feels like one product with the console
 * while keeping its own chrome.
 */
export function StudioSidebar({
  view,
  onView,
  threads,
  activeThreadId,
  onOpenThread,
  onNewChat,
  onDeleteThread,
  onRenameThread,
  threadsLoading = false,
  threadsError = false,
  onRetryThreads,
  hasMoreThreads = false,
  loadingMoreThreads = false,
  onLoadMoreThreads,
  footer,
  open,
  onToggle,
}: {
  view: StudioView;
  onView: (view: StudioView) => void;
  /** Conversations across all accessible agents, newest first. */
  threads: AgentThread[];
  activeThreadId: string | null;
  onOpenThread: (threadId: string) => void;
  onNewChat: () => void;
  /** Removes a conversation. Omit to hide the affordance. */
  onDeleteThread?: (threadId: string) => void;
  /** Renames a conversation. Omit to keep titles read-only. */
  onRenameThread?: (threadId: string, title: string) => void;
  threadsLoading?: boolean;
  threadsError?: boolean;
  onRetryThreads?: () => void;
  hasMoreThreads?: boolean;
  loadingMoreThreads?: boolean;
  onLoadMoreThreads?: () => void;
  /** The account menu, rendered at the bottom of the rail. */
  footer?: React.ReactNode;
  open: boolean;
  onToggle: () => void;
}) {
  const canManageThreads = Boolean(onDeleteThread || onRenameThread);
  return (
    <aside
      data-state={open ? "expanded" : "collapsed"}
      className={cn(
        "studio-sidebar flex h-full shrink-0 flex-col overflow-hidden border-r bg-muted/20 text-foreground transition-[width] duration-200 ease-out motion-reduce:transition-none",
        open ? "w-64" : "w-14",
      )}
    >
      <div
        className={cn(
          "flex h-14 shrink-0 items-center gap-2",
          open ? "px-4" : "justify-center px-2",
        )}
      >
        {open ? (
          <>
            <span className="flex size-9 shrink-0 items-center justify-center p-0.5">
              <img
                src="/images/nova-mark.svg"
                alt=""
                aria-hidden="true"
                className="size-full"
              />
            </span>
            <span className="flex min-w-0 flex-1 items-baseline gap-1.5 text-start">
              <span className="truncate text-lg leading-none font-medium text-primary">
                nova
              </span>
              <span className="truncate text-base leading-none font-medium text-foreground">
                Studio
              </span>
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="ms-auto size-7 text-muted-foreground"
              onClick={onToggle}
              aria-label="Collapse sidebar"
            >
              <PanelLeft aria-hidden="true" className="size-4" />
            </Button>
          </>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="flex size-9 items-center justify-center rounded-md p-0.5 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                onClick={onToggle}
                aria-label="Expand sidebar"
              >
                <img
                  src="/images/nova-mark.svg"
                  alt=""
                  aria-hidden="true"
                  className="size-full"
                />
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">Expand sidebar</TooltipContent>
          </Tooltip>
        )}
      </div>

      <nav className={cn("mt-2 space-y-1", open ? "px-3" : "px-2")}>
        <SidebarNavButton
          label="New chat"
          icon={MessageSquarePlus}
          active={false}
          open={open}
          onClick={onNewChat}
        />
        {NAV.map((item) => (
          <SidebarNavButton
            key={item.id}
            label={item.label}
            icon={item.icon}
            active={view === item.id}
            open={open}
            onClick={() => onView(item.id)}
          />
        ))}
      </nav>

      {open ? (
        <ScrollArea className="mt-4 min-h-0 min-w-0 flex-1 px-3 [&_[data-slot=scroll-area-viewport]>div]:block!">
          {/* Past conversations. Studio used to open every session empty, so a
              conversation you wanted to return to was unreachable. The row
              carries its own relative date: recency is how you find a
              conversation again, and the title alone does not tell you which
              "What was total revenue?" you meant. */}
          <div className="pb-3">
            <div className="flex items-center px-1 pb-1">
              <span className="text-xs font-medium text-muted-foreground">
                History
              </span>
            </div>
            {threadsError ? (
              <div role="alert" className="px-1 py-2 text-xs text-muted-foreground">
                Could not load conversations.
                {onRetryThreads ? <Button variant="link" size="sm" className="h-auto px-1 text-xs" onClick={onRetryThreads}>Retry history</Button> : null}
              </div>
            ) : null}
            {threadsLoading ? (
              <p className="px-1 py-2 text-xs text-muted-foreground">
                Loading conversations
              </p>
            ) : threads.length === 0 && !threadsError && !hasMoreThreads ? (
              <p className="px-1 py-2 text-xs text-muted-foreground">
                No conversations yet. Ask something and it will appear here.
              </p>
            ) : (
              <ul className="space-y-0.5">
                {threads.map((thread) => (
                  <ThreadRow
                    key={thread.thread_id}
                    thread={thread}
                    active={thread.thread_id === activeThreadId}
                    canManage={canManageThreads}
                    onOpen={() => onOpenThread(thread.thread_id)}
                    onDelete={
                      onDeleteThread
                        ? () => onDeleteThread(thread.thread_id)
                        : undefined
                    }
                    onRename={
                      onRenameThread
                        ? (title) => onRenameThread(thread.thread_id, title)
                        : undefined
                    }
                  />
                ))}
              </ul>
            )}
            {hasMoreThreads ? (
              <Button type="button" variant="ghost" size="sm" className="mt-2 h-auto w-full py-3 sm:py-2"
                disabled={loadingMoreThreads} onClick={onLoadMoreThreads}>
                {loadingMoreThreads ? "Loading…" : "Load more conversations"}
              </Button>
            ) : null}
          </div>
        </ScrollArea>
      ) : (
        <div className="min-h-0 flex-1" />
      )}

      {footer ? (
        <div
          className={cn(
            "flex border-t",
            open ? "p-2" : "justify-center px-1.5 py-2",
          )}
        >
          {footer}
        </div>
      ) : null}
    </aside>
  );
}

function SidebarNavButton({
  label,
  icon: Icon,
  active,
  open,
  onClick,
}: {
  label: string;
  icon: LucideIcon;
  active: boolean;
  open: boolean;
  onClick: () => void;
}) {
  const button = (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center rounded-sm text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
        open ? "w-full gap-2 px-3 py-2" : "size-9 justify-center",
        active
          ? "bg-accent text-accent-foreground"
          : "text-foreground hover:bg-accent/50",
      )}
    >
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      {open ? <span className="truncate">{label}</span> : null}
    </button>
  );

  if (open) return button;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

/**
 * One conversation in the history list.
 *
 * The title is the whole point of the row, so it gets the width and stays a
 * single truncated line; the full title is always reachable from the tooltip
 * rather than hidden behind the truncation. Rename turns the row into an input
 * in place, which is the same rectangle the reader is already looking at.
 */
function ThreadRow({
  thread,
  active,
  canManage,
  onOpen,
  onDelete,
  onRename,
}: {
  thread: AgentThread;
  active: boolean;
  canManage: boolean;
  onOpen: () => void;
  onDelete?: () => void;
  onRename?: (title: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(thread.title);
  const [tooltipOpen, setTooltipOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const label = thread.title || "Untitled";
  const timestamp = relativeUpdatedAt(thread.updated_at);

  if (editing && onRename) {
    const commit = () => {
      const next = draft.trim();
      setEditing(false);
      if (next && next !== thread.title) onRename(next);
    };
    return (
      <li>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            commit();
          }}
        >
          <Input
            ref={inputRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={commit}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setDraft(thread.title);
                setEditing(false);
              }
            }}
            aria-label="Conversation title"
            className="h-10 text-sm"
          />
        </form>
      </li>
    );
  }

  return (
    <li
      className={cn(
        "group/thread flex min-w-0 items-center rounded-sm transition-colors hover:bg-accent/50 has-[[data-state=open]]:bg-accent/50",
        active ? "bg-accent text-accent-foreground" : "text-foreground",
      )}
    >
      <Tooltip
        open={tooltipOpen && !menuOpen}
        onOpenChange={setTooltipOpen}
        delayDuration={400}
      >
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={onOpen}
            onDoubleClick={
              onRename
                ? () => {
                    setDraft(thread.title);
                    setEditing(true);
                  }
                : undefined
            }
            aria-current={active ? "true" : undefined}
            className={cn(
              "flex min-w-0 flex-1 flex-col items-start gap-0.5 rounded-sm px-2 py-2 text-left text-sm",
              "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
            )}
          >
            <span className="w-full truncate leading-tight">{label}</span>
            {timestamp ? (
              <span
                className="text-[11px] leading-none text-muted-foreground"
              >
                {timestamp}
              </span>
            ) : null}
          </button>
        </TooltipTrigger>
        <TooltipContent
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={8}
          className="max-w-xs break-words"
        >
          {label}
        </TooltipContent>
      </Tooltip>

      {canManage ? (
        <DropdownMenu
          open={menuOpen}
          onOpenChange={(nextOpen) => {
            setMenuOpen(nextOpen);
            setTooltipOpen(false);
          }}
        >
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className={cn(
                "me-1 size-7 shrink-0 text-muted-foreground hover:bg-background hover:text-foreground data-[state=open]:bg-background data-[state=open]:opacity-100",
                "opacity-100 [@media(hover:hover)]:opacity-0",
                "[@media(hover:hover)]:group-hover/thread:opacity-100",
                "[@media(hover:hover)]:group-focus-within/thread:opacity-100",
              )}
              aria-label={`Actions for ${label}`}
              onPointerEnter={() => setTooltipOpen(false)}
              onFocus={() => setTooltipOpen(false)}
            >
              <MoreHorizontal aria-hidden="true" className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="right" align="start">
            {onRename ? (
              <DropdownMenuItem
                onSelect={() => {
                  setDraft(thread.title);
                  setEditing(true);
                }}
              >
                <Pencil aria-hidden="true" />
                Rename
              </DropdownMenuItem>
            ) : null}
            {onDelete ? (
              <DropdownMenuItem variant="destructive" onSelect={onDelete}>
                <Trash2 aria-hidden="true" />
                Delete
              </DropdownMenuItem>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
    </li>
  );
}
