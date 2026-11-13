import {
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { Download, MoreVertical, Pencil, Plus, X } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { WorkspaceTabState } from "./types";

type TabGeometry = { id: string; left: number; right: number; width: number };
type PendingDrag = {
  id: string;
  pointerId: number;
  startX: number;
  fromIndex: number;
  geometry: TabGeometry[];
  moved: boolean;
};
type DragPreview = {
  id: string;
  fromIndex: number;
  targetIndex: number;
  width: number;
  offsetX: number;
  settling: boolean;
};

function getTabTargetIndex(
  geometry: TabGeometry[],
  draggedId: string,
  draggedCenterX: number,
) {
  return geometry.filter(
    (tab) =>
      tab.id !== draggedId && draggedCenterX > (tab.left + tab.right) / 2,
  ).length;
}

export function WorkspaceTabStrip({
  tabs,
  openTabIds,
  activeTabId,
  onActivate,
  onClose,
  onReorder,
  onRename,
  onNewFile,
}: {
  tabs: Record<string, WorkspaceTabState>;
  openTabIds: string[];
  activeTabId: string | null;
  onActivate: (id: string) => void;
  onClose: (id: string) => void;
  onReorder: (fromId: string, toId: string) => void;
  onRename: (id: string, name: string) => Promise<boolean>;
  onNewFile: () => void;
}) {
  const tabElements = useRef(new Map<string, HTMLDivElement>());
  const pendingDrag = useRef<PendingDrag | null>(null);
  const frame = useRef<number | null>(null);
  const settleTimer = useRef<number | null>(null);
  const suppressClick = useRef(false);
  const renameSubmitting = useRef(false);
  const cancelRename = useRef(false);
  const renameInput = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<DragPreview | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameWidth, setRenameWidth] = useState<number | null>(null);
  const visibleTabIds = openTabIds.filter((id) => tabs[id]);
  const scroller = useRef<HTMLDivElement>(null);

  // Bring the active tab into view inside the strip.
  useEffect(() => {
    const strip = scroller.current;
    const tab = activeTabId ? tabElements.current.get(activeTabId) : null;
    if (!strip || !tab) return;
    const left = tab.offsetLeft;
    if (left < strip.scrollLeft) strip.scrollLeft = left;
    else if (left + tab.offsetWidth > strip.scrollLeft + strip.clientWidth)
      strip.scrollLeft = left + tab.offsetWidth - strip.clientWidth;
  }, [activeTabId, visibleTabIds.length]);

  useEffect(
    () => () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      if (settleTimer.current !== null)
        window.clearTimeout(settleTimer.current);
    },
    [],
  );

  function previewAt(drag: PendingDrag, clientX: number): DragPreview {
    const source = drag.geometry[drag.fromIndex];
    const offsetX = clientX - drag.startX;
    return {
      id: drag.id,
      fromIndex: drag.fromIndex,
      targetIndex: getTabTargetIndex(
        drag.geometry,
        drag.id,
        source.left + source.width / 2 + offsetX,
      ),
      width: source.width,
      offsetX,
      settling: false,
    };
  }

  function startDrag(event: ReactPointerEvent<HTMLButtonElement>, id: string) {
    if (
      !event.isPrimary ||
      event.button !== 0 ||
      renamingId ||
      preview?.settling
    )
      return;
    const geometry = visibleTabIds.flatMap((tabId) => {
      const element = tabElements.current.get(tabId);
      if (!element) return [];
      const { left, right, width } = element.getBoundingClientRect();
      return [{ id: tabId, left, right, width }];
    });
    const fromIndex = geometry.findIndex((tab) => tab.id === id);
    if (fromIndex < 0) return;
    pendingDrag.current = {
      id,
      pointerId: event.pointerId,
      startX: event.clientX,
      fromIndex,
      geometry,
      moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const drag = pendingDrag.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (!drag.moved && Math.abs(event.clientX - drag.startX) < 5) return;
    drag.moved = true;
    const clientX = event.clientX;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      setPreview(previewAt(drag, clientX));
    });
  }

  function finishDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const drag = pendingDrag.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    pendingDrag.current = null;
    if (frame.current !== null) {
      cancelAnimationFrame(frame.current);
      frame.current = null;
    }
    if (!drag.moved) return;
    suppressClick.current = true;
    window.setTimeout(() => {
      suppressClick.current = false;
    }, 0);
    const next = previewAt(drag, event.clientX);
    const target = drag.geometry[next.targetIndex];
    const source = drag.geometry[drag.fromIndex];
    const destinationLeft =
      next.targetIndex > drag.fromIndex
        ? target.right - source.width
        : target.left;
    setPreview({
      ...next,
      offsetX: destinationLeft - source.left,
      settling: true,
    });
    settleTimer.current = window.setTimeout(() => {
      if (next.targetIndex !== drag.fromIndex) onReorder(drag.id, target.id);
      setPreview(null);
      settleTimer.current = null;
    }, 150);
  }

  function cancelDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    if (pendingDrag.current?.pointerId !== event.pointerId) return;
    pendingDrag.current = null;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    setPreview(null);
  }

  function startRename(id: string, title: string) {
    cancelRename.current = false;
    setRenameWidth(
      tabElements.current.get(id)?.getBoundingClientRect().width ?? null,
    );
    setRenameValue(title);
    setRenamingId(id);
  }

  async function finishRename(value = renameValue) {
    if (!renamingId || renameSubmitting.current) return;
    const title = tabs[renamingId]?.title;
    const name = value.trim();
    if (!name || name === title) {
      setRenamingId(null);
      return;
    }
    renameSubmitting.current = true;
    const success = await onRename(renamingId, name);
    renameSubmitting.current = false;
    if (success) setRenamingId(null);
    else requestAnimationFrame(() => renameInput.current?.focus());
  }

  return (
    <div className="flex min-w-0 flex-1 items-end gap-0">
      {/* Tabs scroll here, not the workspace: an overflowing strip would push
          the whole layout sideways when a tab is focused. pb-px/-mb-px keep the
          active tab's overlap with the bottom border. */}
      <div
        ref={scroller}
        className="relative -mb-px flex min-w-0 items-end overflow-x-auto overflow-y-hidden pb-px [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {visibleTabIds.map((id, index) => {
          const tab = tabs[id];
          if (!tab) return null;
          const isActive = activeTabId === id;
          const isRenaming = renamingId === id;
          const isDragging = preview?.id === id;
          const shifted =
            preview &&
            !isDragging &&
            (preview.targetIndex > preview.fromIndex &&
            index > preview.fromIndex &&
            index <= preview.targetIndex
              ? -preview.width
              : preview.targetIndex < preview.fromIndex &&
                  index >= preview.targetIndex &&
                  index < preview.fromIndex
                ? preview.width
                : 0);
          const offsetX = isDragging ? preview.offsetX : shifted || 0;
          const tabStyle = {
            ...(isRenaming && renameWidth ? { width: renameWidth } : {}),
            ...(offsetX
              ? { transform: `translate3d(${offsetX}px, 0, 0)` }
              : {}),
          };
          const tabClass = cn(
            "flex min-w-0 flex-1 items-center rounded-t-md py-1.5 text-sm",
            isActive
              ? "-mb-px border-x border-t-2 border-x-border border-t-primary border-b-0 bg-background text-primary"
              : "border border-border/80 bg-muted/40 text-muted-foreground hover:bg-muted/60",
          );
          return (
            <div
              key={id}
              ref={(element) => {
                if (element) tabElements.current.set(id, element);
                else tabElements.current.delete(id);
              }}
              style={tabStyle}
              className={cn(
                "group relative flex min-w-[120px] max-w-[260px] shrink-0 items-center",
                // Neighbours share one outline instead of doubling it.
                index > 0 && "-ml-px",
                isActive && "z-10",
                preview && "will-change-transform",
                preview &&
                  (!isDragging || preview.settling) &&
                  "transition-transform duration-150 ease-out",
                isDragging && "z-30 shadow-md",
              )}
            >
              {isRenaming ? (
                <div className={cn(tabClass, "w-full px-3")}>
                  <Input
                    ref={renameInput}
                    autoFocus
                    aria-label={`Rename ${tab.title}`}
                    value={renameValue}
                    onChange={(event) => setRenameValue(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        if (event.nativeEvent.isComposing) return;
                        event.preventDefault();
                        event.stopPropagation();
                        void finishRename(event.currentTarget.value);
                      }
                      if (event.key === "Escape") {
                        event.stopPropagation();
                        cancelRename.current = true;
                        setRenamingId(null);
                      }
                    }}
                    onBlur={(event) => {
                      if (!cancelRename.current)
                        void finishRename(event.currentTarget.value);
                    }}
                    onFocus={(event) => {
                      const dot = renameValue.lastIndexOf(".");
                      event.target.setSelectionRange(
                        0,
                        dot > 0 ? dot : renameValue.length,
                      );
                    }}
                    className="h-5 min-w-0 flex-1 border-0 bg-transparent p-0 text-sm text-inherit shadow-none outline-none focus-visible:ring-0 focus-visible:ring-offset-0"
                  />
                </div>
              ) : (
                <>
                  <button
                    type="button"
                    onPointerDown={(event) => startDrag(event, id)}
                    onPointerMove={moveDrag}
                    onPointerUp={finishDrag}
                    onPointerCancel={cancelDrag}
                    onClick={() => {
                      if (!suppressClick.current) onActivate(id);
                    }}
                    onKeyDown={(event) => {
                      if (
                        !event.altKey ||
                        !["ArrowLeft", "ArrowRight"].includes(event.key)
                      )
                        return;
                      const neighbor =
                        visibleTabIds[
                          index + (event.key === "ArrowLeft" ? -1 : 1)
                        ];
                      if (!neighbor) return;
                      event.preventDefault();
                      onReorder(id, neighbor);
                    }}
                    className={cn(
                      tabClass,
                      "w-full cursor-grab touch-none select-none gap-2 pr-14 pl-3 transition-colors active:cursor-grabbing",
                    )}
                  >
                    <span className="truncate">{tab.title}</span>
                  </button>
                  <div
                    className={cn(
                      "absolute right-1 z-20 flex items-center gap-0.5 rounded-sm transition-opacity",
                      isActive
                        ? "opacity-100"
                        : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100",
                    )}
                  >
                    <TabMenuButton
                      title={tab.title}
                      onRename={() => startRename(id, tab.title)}
                      onDownload={() => {
                        const blob = new Blob([tab.content], {
                          type: "text/sql",
                        });
                        const url = URL.createObjectURL(blob);
                        const link = document.createElement("a");
                        link.href = url;
                        link.download = tab.title.endsWith(".sql")
                          ? tab.title
                          : `${tab.title}.sql`;
                        link.click();
                        URL.revokeObjectURL(url);
                      }}
                      onClose={() => onClose(id)}
                    />
                    <button
                      type="button"
                      aria-label={`Close ${tab.title}`}
                      className="flex size-5 items-center justify-center rounded-sm hover:bg-muted-foreground/20"
                      onClick={() => onClose(id)}
                    >
                      <X className="size-3.5" />
                    </button>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
      <button
        type="button"
        className="mb-0.5 ml-0.5 shrink-0 rounded-t-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        title="New file"
        onClick={onNewFile}
      >
        <Plus className="size-3.5" />
      </button>
    </div>
  );
}

function TabMenuButton({
  title,
  onRename,
  onDownload,
  onClose,
}: {
  title: string;
  onRename: () => void;
  onDownload: () => void;
  onClose: () => void;
}) {
  // A portaled menu: the tab strip scrolls horizontally and would clip it.
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Options for ${title}`}
          className="flex size-5 items-center justify-center rounded-sm hover:bg-muted-foreground/20"
        >
          <MoreVertical className="size-3.5" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-36"
        // Rename moves focus into the tab's name field; keep it there.
        onCloseAutoFocus={(event) => event.preventDefault()}
      >
        <DropdownMenuItem onSelect={onRename}>
          <Pencil className="size-3.5" /> Rename
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onDownload}>
          <Download className="size-3.5" /> Download SQL
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={onClose}>
          <X className="size-3.5" /> Close
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
