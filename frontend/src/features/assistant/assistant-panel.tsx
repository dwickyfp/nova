import { useEffect, useRef, useState } from "react";
import { MessageSquarePlus, PanelRightClose, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import type { SelectedModel } from "./assistant-provider";
import { ASSISTANT_DEFAULT_WIDTH } from "./assistant-panel-state";
import { AssistantComposer } from "./assistant-composer";
import { AssistantEmptyState } from "./assistant-empty-state";
import type { ApprovalMode } from "./use-assistant-turn";
import { MessageList } from "./message-list";
import type { TurnContext } from "./stream-client";
import type { NoveSuggestedAction } from "./surface-registry";
import type { NoveEventInput } from "./app-context";
import { PanelResizeHandle } from "./panel-resize-handle";
import { ThreadHistory } from "./thread-history";
import type { ThreadView } from "./thread-client";
import type { ToolCallCardProps } from "./tool-call-card";
import type { TranscriptMessage } from "./use-assistant-transcript";
import type { AttachedQuery } from "./query-attach";
import { useIsNarrowForAssistant } from "./use-assistant-panel";

export type AssistantPanelProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Overrides the built-in transcript rendering when supplied. */
  children?: React.ReactNode;
  onSendMessage?: (message: string) => void;
  disabled?: boolean;
  /** Transcript state. When omitted the panel shows the empty state. */
  messages?: TranscriptMessage[];
  onDecide?: ToolCallCardProps["onDecide"];
  decidingToolCallId?: string | null;
  /** True while a turn is streaming; swaps Send for Stop. */
  streaming?: boolean;
  onStop?: () => void;
  /** Announced to assistive tech on state transitions, never per token. */
  statusMessage?: string | null;
  /** Model pinned for the next turn; null lets the backend choose. */
  selectedModel?: SelectedModel;
  onSelectModel?: (model: SelectedModel) => void;
  /** How the next read-only query is approved, shown in the composer. */
  approvalMode?: ApprovalMode;
  onSelectApprovalMode?: (mode: ApprovalMode) => void;
  /** True while an approval-mode change is being persisted. */
  settlingApprovalMode?: boolean;
  /** Clears the conversation so the next message starts a fresh thread. */
  onNewChat?: () => void;
  /** Switches the panel to an existing thread. */
  onOpenThread?: (threadId: string) => void | Promise<void>;
  activeThreadId?: string | null;
  /** True while an existing thread's messages are being fetched. */
  loadingThread?: boolean;
  hasOlderMessages?: boolean;
  loadingOlderMessages?: boolean;
  onLoadOlderMessages?: () => Promise<void>;
  /** Panel width in px (wide mode). Defaults to the standard width when absent. */
  width?: number;
  /** Commits a new panel width after a resize gesture. */
  onResize?: (width: number) => void;
  /**
   * Database/schema/role a code card's Run button executes against. Omitted in
   * a bare panel (no provider), where Run is hidden.
   */
  activeContext?: TurnContext;
  /** Queries attached from the workspace, shown as badges above the composer. */
  attachments?: AttachedQuery[];
  onRemoveAttachment?: (id: string) => void;
  /** Label shown at the left of the header (workspace file, or the default). */
  title?: string;
  /** Name the empty state's greeting addresses. */
  userName?: string | null;
  /** Recent conversations listed in the empty state; omitted hides the section. */
  recentThreads?: ThreadView[];
  suggestedActions?: readonly NoveSuggestedAction[];
  clientActionStatus?: string | null;
  onExecutionEvent?: (event: NoveEventInput) => void;
  onFixWithNove?: (prompt: string) => void;
};

function AssistantBody({
  children,
  onSendMessage,
  disabled,
  messages,
  onDecide,
  decidingToolCallId,
  streaming,
  onStop,
  statusMessage,
  loadingThread,
  hasOlderMessages,
  loadingOlderMessages,
  onLoadOlderMessages,
  selectedModel,
  onSelectModel,
  approvalMode,
  onSelectApprovalMode,
  settlingApprovalMode,
  activeContext,
  attachments,
  onRemoveAttachment,
  userName,
  onOpenThread,
  activeThreadId,
  recentThreads,
  suggestedActions,
  clientActionStatus,
  onExecutionEvent,
  onFixWithNove,
}: AssistantPanelProps) {
  const hasTranscript = Boolean(children) || Boolean(messages?.length);
  const scrollRef = useRef<HTMLDivElement>(null);
  const prependScrollRef = useRef<{ height: number; top: number } | null>(null);
  const lastMessage = messages?.[messages.length - 1];
  // Signature of the transcript's visible state, so auto-scroll follows both a
  // new message and each streamed delta appended to the last one.
  const transcriptSignal = `${messages?.length ?? 0}:${lastMessage?.content.length ?? 0}`;

  useEffect(() => {
    // Scroll the transcript viewport itself, not an ancestor: a sentinel plus
    // `scrollIntoView` can scroll the page when the panel is inside a scrolling
    // document, which dragged the whole layout. The viewport is the node Radix
    // marks with `data-slot`, and it is the only element that should move.
    const viewport = scrollRef.current?.querySelector<HTMLElement>(
      "[data-slot=scroll-area-viewport]",
    );
    if (viewport) {
      const previous = prependScrollRef.current;
      if (previous) {
        viewport.scrollTop = previous.top + viewport.scrollHeight - previous.height;
        prependScrollRef.current = null;
      } else {
        viewport.scrollTop = viewport.scrollHeight;
      }
    }
  }, [transcriptSignal, loadingThread]);
  useEffect(() => {
    if (!loadingOlderMessages) prependScrollRef.current = null;
  }, [loadingOlderMessages]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* `h-0` + `flex-1` pins the scroll area to the leftover space in the
          column, so the transcript scrolls inside the panel rather than the
          page growing taller. */}
      <ScrollArea ref={scrollRef} className="h-0 min-h-0 flex-1">
        <div className="flex min-h-full w-0 min-w-full flex-col p-3">
          {hasOlderMessages && !loadingThread ? (
            <Button type="button" variant="ghost" size="sm" className="mb-2 h-auto self-center py-3 sm:py-2"
              disabled={loadingOlderMessages || streaming} onClick={async () => {
                const viewport = scrollRef.current?.querySelector<HTMLElement>("[data-slot=scroll-area-viewport]");
                if (viewport) prependScrollRef.current = { height: viewport.scrollHeight, top: viewport.scrollTop };
                await onLoadOlderMessages?.();
              }}>
              {loadingOlderMessages ? "Loading…" : "Load older messages"}
            </Button>
          ) : null}
          {loadingThread ? (
            <p className="p-4 text-center text-xs text-muted-foreground">
              Loading conversation…
            </p>
          ) : (
            (children ??
            (hasTranscript ? (
              <MessageList
                messages={messages ?? []}
                onDecide={onDecide}
                decidingToolCallId={decidingToolCallId}
                statusMessage={statusMessage}
                activeContext={activeContext}
                onExecutionEvent={onExecutionEvent}
                onFixWithNove={onFixWithNove}
              />
            ) : (
              <AssistantEmptyState
                userName={userName}
                onSendMessage={onSendMessage}
                onOpenThread={onOpenThread}
                activeThreadId={activeThreadId}
                recentThreads={recentThreads}
                surfaceTitle={activeContext?.appContext?.surface.title}
                suggestedActions={suggestedActions}
              />
            )))
          )}
        </div>
      </ScrollArea>
      {clientActionStatus ? (
        <p role="status" className="border-t px-3 py-2 text-xs text-muted-foreground">
          {clientActionStatus}
        </p>
      ) : null}
      <AssistantComposer
        onSendMessage={onSendMessage}
        streaming={streaming}
        onStop={onStop}
        disabled={disabled}
        selectedModel={selectedModel}
        onSelectModel={onSelectModel}
        approvalMode={approvalMode}
        onSelectApprovalMode={onSelectApprovalMode}
        settlingApprovalMode={settlingApprovalMode}
        attachments={attachments}
        onRemoveAttachment={onRemoveAttachment}
      />
    </div>
  );
}

function AssistantHeader({
  onClose,
  onNewChat,
  onOpenThread,
  activeThreadId,
  busy,
  title,
}: {
  onClose: () => void;
  onNewChat?: () => void;
  onOpenThread?: (threadId: string) => void | Promise<void>;
  activeThreadId?: string | null;
  busy?: boolean;
  title?: string;
}) {
  return (
    // Deliberately borderless: the header floats over the panel so the surface
    // reads as one continuous sheet rather than a framed card.
    <div className="flex items-center gap-2 px-3 py-2">
      <img
        src="/images/nova-mark.svg"
        alt=""
        aria-hidden="true"
        className="size-4 shrink-0"
      />
      <h2 className="min-w-0 flex-1 truncate text-sm font-medium">
        {title ?? "Nove"}
      </h2>
      <div className="flex shrink-0 items-center gap-0">
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className="grid size-9 shrink-0 place-items-center text-success-strong"
              aria-label="Enterprise data protection"
            >
              <ShieldCheck aria-hidden="true" className="size-4" />
            </span>
          </TooltipTrigger>
          <TooltipContent
            arrowClassName="!bg-background !fill-background border-r border-b"
            className="border bg-background text-foreground"
          >
            <div className="text-sm">
              <a
                href="https://docs.starrocks.io"
                target="_blank"
                rel="noreferrer"
                className="block font-medium text-primary underline underline-offset-2"
              >
                Enterprise data protection
              </a>
              <p>applies to this chat.</p>
            </div>
          </TooltipContent>
        </Tooltip>
        {onNewChat ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={onNewChat}
            disabled={busy}
            aria-label="New chat"
          >
            <MessageSquarePlus aria-hidden="true" className="size-4" />
          </Button>
        ) : null}
        {onOpenThread ? (
          <ThreadHistory
            activeThreadId={activeThreadId ?? null}
            onOpenThread={onOpenThread}
            disabled={busy}
          />
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label="Close assistant"
        >
          <PanelRightClose className="size-4" />
        </Button>
      </div>
    </div>
  );
}

export function AssistantPanel(props: AssistantPanelProps) {
  const { open, onOpenChange } = props;
  const isNarrow = useIsNarrowForAssistant();
  const busy = Boolean(props.streaming) || Boolean(props.loadingThread);
  // Live width during a drag, so the panel edge tracks the pointer instead of
  // only snapping to the committed value on release.
  const [previewWidth, setPreviewWidth] = useState<number | null>(null);

  const header = (
    <AssistantHeader
      onClose={() => onOpenChange(false)}
      onNewChat={props.onNewChat}
      onOpenThread={props.onOpenThread}
      activeThreadId={props.activeThreadId}
      busy={busy}
      title={props.title}
    />
  );

  if (isNarrow) {
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          side="right"
          className="flex w-[22rem] flex-col p-0 sm:max-w-[22rem]"
        >
          <SheetTitle className="sr-only">Nove</SheetTitle>
          <div className="flex h-full min-h-0 flex-col">
            {header}
            <AssistantBody {...props} />
          </div>
        </SheetContent>
      </Sheet>
    );
  }

  const committedWidth = props.width ?? ASSISTANT_DEFAULT_WIDTH;
  const width = previewWidth ?? committedWidth;
  const displayWidth = open ? width : 0;
  const dragging = previewWidth !== null;

  return (
    // The wrapper animates its width so the panel slides rather than blinking
    // in and out. The width is inline (a drag-resized value cannot be a static
    // class) and the transition is suspended while dragging so the edge tracks
    // the pointer instead of lagging behind it. `h-full` + `min-h-0` are what
    // bound the transcript's scroll area to the viewport. The aside stays
    // mounted while closed, so `inert` (not unmounting) keeps its transcript
    // and controls out of the tab order and off the accessibility tree.
    <div
      data-state={open ? "open" : "closed"}
      style={{ width: displayWidth }}
      className={cn(
        "relative h-full min-h-0 shrink-0 overflow-hidden",
        !dragging &&
          "transition-[width] duration-200 ease-in-out motion-reduce:transition-none",
      )}
    >
      {open && props.onResize ? (
        <PanelResizeHandle
          width={committedWidth}
          onResize={props.onResize}
          onPreview={setPreviewWidth}
        />
      ) : null}
      <aside
        id="assistant-panel"
        aria-label="Nove"
        inert={!open}
        style={{ width }}
        className="flex h-full min-h-0 flex-col border-l bg-background"
      >
        {/* Closing lives in the header now, so the layout FAB is only the
            open control and is hidden while the panel is open. */}
        {header}
        <AssistantBody {...props} />
      </aside>
    </div>
  );
}
