import { useCallback, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  ASSISTANT_OFFSET_Y_STEP,
  clampAssistantOffsetY,
} from "./assistant-panel-state";
import { useAssistant } from "./assistant-provider";

type DragState = {
  pointerId: number;
  startY: number;
  startOffset: number;
  rect: { top: number; bottom: number };
  /** True once the pointer has moved past the click threshold. */
  moved: boolean;
};

/**
 * Movement (px) before a press turns into a drag. Below it the gesture stays a
 * click, so pressing the button to open the panel does not nudge its position.
 */
const DRAG_THRESHOLD = 4;

/** Grip geometry: a two-column, three-row block of dots. */
const GRIP_DOTS = Array.from({ length: 6 }, (_, index) => index);

export type AssistantToggleProps = {
  /**
   * When true the whole button is a drag handle for its vertical position; a
   * press that does not move still opens the panel. When false — a bare toggle
   * with no provider — the button is not draggable.
   */
  draggable?: boolean;
};

/**
 * The floating way to open the assistant, anchored to the right edge. It only
 * renders while the panel is closed: once open, the close control lives in the
 * panel header (right side), so there is one non-floating place to close it
 * instead of a FAB overlapping the header. On narrow viewports the same button
 * is the `Sheet` trigger.
 *
 * Press-and-drag anywhere on the button moves it vertically; the offset is
 * persisted, so the resting place survives a reload. A press that moves less
 * than `DRAG_THRESHOLD` is a click and opens the panel, so the two gestures do
 * not compete. Arrow up/down move it from the keyboard while focused. On hover
 * a grip of dots slides in on the left as the affordance and a tooltip names
 * the action.
 */
export function AssistantToggle({ draggable = false }: AssistantToggleProps) {
  const { open, toggle, offsetY, setOffsetY } = useAssistant();
  const draggingRef = useRef<DragState | null>(null);
  // `draggingRef` drives the gesture (read inside window listeners); this state
  // mirrors it for rendering, so the wrapper can drop its `bottom` transition
  // while the pointer owns the value and the edge tracks instead of lagging.
  const [dragging, setDragging] = useState(false);
  // The click event fires after pointerup, once `draggingRef` is cleared. This
  // flag carries "that gesture was a drag" across to the click handler, which
  // then swallows the click so a drag does not also open the panel.
  const suppressClickRef = useRef(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const onPointerMove = useCallback(
    (event: PointerEvent) => {
      const drag = draggingRef.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      const delta = drag.startY - event.clientY;
      if (!drag.moved) {
        if (Math.abs(delta) < DRAG_THRESHOLD) return;
        drag.moved = true;
        setDragging(true);
      }
      setOffsetY(
        clampAssistantOffsetY(
          drag.startOffset + delta,
          drag.rect,
          window.innerHeight,
        ),
      );
    },
    [setOffsetY],
  );

  const stop = useCallback(
    (event: PointerEvent) => {
      const drag = draggingRef.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      // Remember whether this gesture dragged, for the click that follows. A
      // cancelled pointer has no click, so only a real drag on pointerup needs
      // the flag; simply mirroring `moved` is harmless for both.
      suppressClickRef.current = drag.moved;
      draggingRef.current = null;
      setDragging(false);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    },
    [onPointerMove],
  );

  const start = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      if (!draggable || event.button !== 0) return;
      const button = buttonRef.current;
      if (!button) return;
      // The rect already sits at `startOffset`, so a same-offset clamp leaves
      // the bounds consistent for the whole gesture.
      const rect = button.getBoundingClientRect();
      // Best-effort capture so the gesture keeps receiving moves off the button.
      // It throws for a pointer that is not active (e.g. a synthetic event in a
      // test), which must not abort starting the gesture.
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // Ignore: the window listeners below still drive the gesture.
      }
      draggingRef.current = {
        pointerId: event.pointerId,
        startY: event.clientY,
        startOffset: offsetY,
        rect: { top: rect.top, bottom: rect.bottom },
        moved: false,
      };
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", stop);
      window.addEventListener("pointercancel", stop);
    },
    [draggable, offsetY, onPointerMove, stop],
  );

  const onClick = useCallback(() => {
    // A completed drag must not also toggle the panel: the pointerup set this
    // flag, and the click it produced is swallowed here.
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    toggle();
  }, [toggle]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLElement>) => {
      if (!draggable) return;
      const button = buttonRef.current;
      if (!button) return;
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      event.preventDefault();
      const rect = button.getBoundingClientRect();
      const delta =
        event.key === "ArrowUp"
          ? ASSISTANT_OFFSET_Y_STEP
          : -ASSISTANT_OFFSET_Y_STEP;
      setOffsetY(
        clampAssistantOffsetY(offsetY + delta, rect, window.innerHeight),
      );
    },
    [draggable, offsetY, setOffsetY],
  );

  if (open) return null;

  const label = "Ask Nove";

  return (
    <div
      className="group fixed right-0 z-50 flex items-center"
      style={{
        bottom: `calc(1rem + ${offsetY}px)`,
        transition: dragging ? undefined : "bottom 200ms ease-out",
      }}
    >
      {draggable ? (
        <span
          aria-hidden="true"
          className="grid w-0 grid-cols-2 grid-rows-3 gap-0.5 overflow-hidden opacity-0 transition-[width,opacity] duration-200 ease-out group-hover:mr-1 group-hover:w-3 group-hover:opacity-100 group-focus-within:mr-1 group-focus-within:w-3 group-focus-within:opacity-100 motion-reduce:transition-none"
        >
          {GRIP_DOTS.map((index) => (
            <span
              key={index}
              className="size-1 rounded-full bg-primary-foreground/70"
            />
          ))}
        </span>
      ) : null}

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            ref={buttonRef}
            type="button"
            size="icon"
            variant="default"
            className={
              "min-h-12 min-w-12 rounded-l-lg rounded-r-none px-3 shadow-lg transition-transform duration-200 motion-reduce:transition-none" +
              (draggable ? " cursor-row-resize touch-none" : "")
            }
            aria-label={label}
            aria-pressed={open}
            aria-expanded={open}
            aria-controls="assistant-panel"
            onPointerDown={start}
            onKeyDown={onKeyDown}
            onClick={onClick}
          >
            <Sparkles aria-hidden="true" className="size-5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="left">{label}</TooltipContent>
      </Tooltip>
    </div>
  );
}
