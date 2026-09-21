import { useEffect, useRef, useState } from "react";
import {
  LayoutGrid,
  MessageSquarePlus,
  Package,
  PanelLeft,
  Pencil,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { AgentThread } from "@/features/agents/api";
import { relativeUpdatedAt } from "./thread-time";

export type StudioView = "chat" | "artifacts" | "capabilities";

const NAV: {
  id: Exclude<StudioView, "chat">;
  label: string;
  icon: LucideIcon;
}[] = [
  { id: "artifacts", label: "Artifacts", icon: Package },
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
  footer,
  open,
  onToggle,
}: {
  view: StudioView;
  onView: (view: StudioView) => void;
  /** Past conversations for the active agent, newest first. */
  threads: AgentThread[];
  activeThreadId: string | null;
  onOpenThread: (threadId: string) => void;
  onNewChat: () => void;
  /** Removes a conversation. Omit to hide the affordance. */
  onDeleteThread?: (threadId: string) => void;
  /** Renames a conversation. Omit to keep titles read-only. */
  onRenameThread?: (threadId: string, title: string) => void;
  threadsLoading?: boolean;
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
        "flex h-full shrink-0 flex-col overflow-hidden border-r bg-muted/20 transition-[width] duration-200 ease-out",
        open ? "w-72" : "w-14",
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
            <span className="grid min-w-0 flex-1 gap-1 text-start leading-tight">
              <span className="truncate font-manrope text-lg leading-none font-semibold text-primary">
                nova
              </span>
              <span className="truncate text-[11px] leading-none font-medium text-muted-foreground">
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
        <ScrollArea className="mt-4 min-h-0 flex-1 px-3">
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
            {threadsLoading ? (
              <p className="px-1 py-2 text-xs text-muted-foreground">
                Loading conversations
              </p>
            ) : threads.length === 0 ? (
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
        "flex items-center rounded-md text-sm transition-colors",
        open ? "w-full gap-2 px-3 py-2" : "size-9 justify-center",
        active
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:bg-accent/50",
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
    <li className="group/thread relative">
      <Tooltip>
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
              "flex w-full flex-col items-start gap-0.5 rounded-md py-2 text-left text-sm transition-colors",
              "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
              canManage ? "ps-2 pe-8" : "px-2",
              active
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-accent/50",
            )}
          >
            <span className="w-full truncate leading-tight">{label}</span>
            {timestamp ? (
              <span
                className={cn(
                  "text-[11px] leading-none",
                  active
                    ? "text-accent-foreground/70"
                    : "text-muted-foreground/70",
                )}
              >
                {timestamp}
              </span>
            ) : null}
          </button>
        </TooltipTrigger>
        <TooltipContent side="right" className="max-w-xs">
          {label}
        </TooltipContent>
      </Tooltip>

      {/* The actions ride the row on hover for a pointer, and are always shown
          on a touch device, where there is no hover to reveal them. The row
          reserves their width (`pe-8`) either way, so nothing overlaps the
          title. */}
      {canManage ? (
        <div
          className={cn(
            "absolute end-1 top-1/2 flex -translate-y-1/2 items-center gap-0.5 transition-opacity",
            "opacity-100 [@media(hover:hover)]:opacity-0",
            "[@media(hover:hover)]:group-hover/thread:opacity-100",
            "[@media(hover:hover)]:group-focus-within/thread:opacity-100",
          )}
        >
          {onRename ? (
            <button
              type="button"
              onClick={() => {
                setDraft(thread.title);
                setEditing(true);
              }}
              className="rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              aria-label={`Rename ${label}`}
            >
              <Pencil aria-hidden="true" className="size-3.5" />
            </button>
          ) : null}
          {onDelete ? (
            <button
              type="button"
              onClick={onDelete}
              className="rounded p-1 text-muted-foreground hover:bg-background hover:text-destructive focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              aria-label={`Delete ${label}`}
            >
              <Trash2 aria-hidden="true" className="size-3.5" />
            </button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
