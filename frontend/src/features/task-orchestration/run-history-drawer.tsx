import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { ChevronDown, History } from "lucide-react";
import { cn } from "@/lib/utils";
import { RunHistory } from "./run-history";

/** Height when the drawer is open, in pixels. */
const DEFAULT_HEIGHT = 320;
/** The visible height of the header strip when the drawer is collapsed. */
const COLLAPSED_HEIGHT = 44;
const MIN_HEIGHT = 160;
/** Room reserved below the drawer for the page padding. */
const RESERVED = 96;

/**
 * The run history as a bottom drawer over the flow, like the results panel in
 * the SQL workspace: a floating panel with side margins, a grab handle, and a
 * collapse control. The flow behind it stays full-bleed, so the drawer reads as
 * an overlay rather than a split that shrinks the graph.
 *
 * Resizing is pointer-driven and clamps against the container height, so the
 * drawer can never grow past the space that exists even mid-drag.
 */
export function RunHistoryDrawer({
  graphId,
  containerRef,
  onInsetChange,
}: {
  graphId: string;
  containerRef: React.RefObject<HTMLDivElement | null>;
  /** Called with the height the drawer currently occupies (0 when collapsed). */
  onInsetChange?: (inset: number) => void;
}) {
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [collapsed, setCollapsed] = useState(false);
  const [resizing, setResizing] = useState(false);
  const dragRef = useRef<{ startY: number; startHeight: number } | null>(null);

  // Report the footprint the flow must avoid. A collapsed drawer still shows a
  // header strip, so it reports that strip rather than nothing; the flow's fit
  // needs the visible overlay height, not just the open height.
  useEffect(() => {
    onInsetChange?.(collapsed ? COLLAPSED_HEIGHT : height);
  }, [collapsed, height, onInsetChange]);

  const maxHeight = useCallback(() => {
    const available = containerRef.current?.clientHeight ?? 0;
    return Math.max(MIN_HEIGHT, available - RESERVED);
  }, [containerRef]);

  const onPointerMove = useCallback(
    (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      // The drawer is anchored to the bottom, so dragging up increases height.
      const delta = drag.startY - event.clientY;
      const next = Math.min(
        Math.max(drag.startHeight + delta, MIN_HEIGHT),
        maxHeight(),
      );
      setHeight(next);
    },
    [maxHeight],
  );

  const stop = useCallback(() => {
    dragRef.current = null;
    setResizing(false);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", stop);
  }, [onPointerMove]);

  const start = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      if (collapsed) return;
      event.preventDefault();
      dragRef.current = { startY: event.clientY, startHeight: height };
      setResizing(true);
      document.body.style.cursor = "row-resize";
      document.body.style.userSelect = "none";
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", stop);
    },
    [collapsed, height, onPointerMove, stop],
  );

  useEffect(() => stop, [stop]);

  // A shrinking viewport must not leave the open drawer taller than the space.
  useEffect(() => {
    setHeight((current) => Math.min(current, maxHeight()));
  }, [maxHeight]);

  return (
    <div
      style={
        {
          height: `${collapsed ? COLLAPSED_HEIGHT : height}px`,
        } satisfies CSSProperties
      }
      className={cn(
        "pointer-events-auto mx-4 mb-3 flex shrink-0 flex-col overflow-hidden rounded-xl border bg-background shadow-lg",
        resizing
          ? "transition-none"
          : "transition-[height] duration-200 ease-in-out",
      )}
    >
      {!collapsed ? (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize run history"
          aria-valuenow={height}
          aria-valuemin={MIN_HEIGHT}
          className="group flex h-3 w-full shrink-0 cursor-row-resize touch-none items-center justify-center bg-muted/15 hover:bg-muted/30"
          onPointerDown={start}
        >
          <span className="h-1 w-16 rounded-full bg-border transition-colors group-hover:bg-muted-foreground/40" />
        </div>
      ) : null}

      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
        <button
          type="button"
          aria-label={collapsed ? "Expand run history" : "Collapse run history"}
          aria-expanded={!collapsed}
          className="rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          onClick={() => setCollapsed((prev) => !prev)}
        >
          <ChevronDown
            className={cn(
              "size-4 transition-transform",
              collapsed && "-rotate-90",
            )}
          />
        </button>
        <span className="flex items-center gap-1.5 text-xs font-medium">
          <History
            aria-hidden="true"
            className="size-3.5 text-muted-foreground"
          />
          Run history
        </span>
      </div>

      {!collapsed ? (
        <div className="min-h-0 flex-1 overflow-hidden p-3">
          <RunHistory graphId={graphId} />
        </div>
      ) : null}
    </div>
  );
}
