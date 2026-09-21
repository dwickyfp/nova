import { useCallback, useEffect, useRef, useState } from "react";
import { clampAssistantWidth } from "./assistant-panel-state";

export type PanelResizeOptions = {
  width: number;
  onCommit: (width: number) => void;
  /** Live width while dragging, or null when the drag ends. */
  onLiveWidth?: (width: number | null) => void;
};

/**
 * Pointer-driven width resize for the assistant panel. The panel is anchored to
 * the right edge, so dragging the handle left *increases* the panel width, which
 * is why the delta is subtracted from the starting pointer x.
 *
 * The final `pointerup` is observed on `window`, so a drop outside the handle
 * still commits. Widths are clamped on every move, so the persisted value can
 * never leave the allowed range.
 */
export function usePanelResize({
  width,
  onCommit,
  onLiveWidth,
}: PanelResizeOptions) {
  const [dragging, setDragging] = useState(false);
  const stateRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const [preview, setPreview] = useState<number | null>(null);
  // Mirror of ``preview`` so the pointerup handler can read the final width
  // without doing work inside a state updater.
  const previewRef = useRef<number | null>(null);

  const onPointerMove = useCallback(
    (event: PointerEvent) => {
      const state = stateRef.current;
      if (!state) return;
      // Right-anchored panel: moving the pointer left widens it.
      const next = clampAssistantWidth(
        state.startWidth + (state.startX - event.clientX),
      );
      previewRef.current = next;
      setPreview(next);
      onLiveWidth?.(next);
    },
    [onLiveWidth],
  );

  const stop = useCallback(() => {
    const state = stateRef.current;
    const finalWidth = previewRef.current;
    stateRef.current = null;
    previewRef.current = null;
    setDragging(false);
    setPreview(null);
    // Side effects (the parent's commit, and clearing its live width) run here,
    // not inside a state updater: a setState callback must stay pure, and React
    // warns when it triggers another component's update.
    if (state && finalWidth !== null) onCommit(finalWidth);
    onLiveWidth?.(null);
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", stop);
  }, [onCommit, onLiveWidth, onPointerMove]);

  const start = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      event.preventDefault();
      stateRef.current = { startX: event.clientX, startWidth: width };
      previewRef.current = width;
      setDragging(true);
      setPreview(width);
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", stop);
    },
    [onPointerMove, stop, width],
  );

  useEffect(() => {
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", stop);
    };
  }, [onPointerMove, stop]);

  return { dragging, preview, start };
}
