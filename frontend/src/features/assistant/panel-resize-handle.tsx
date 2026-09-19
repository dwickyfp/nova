import { useCallback } from 'react'
import { cn } from '@/lib/utils'
import {
  ASSISTANT_MAX_WIDTH,
  ASSISTANT_MIN_WIDTH,
  clampAssistantWidth,
} from './assistant-panel-state'
import { usePanelResize } from './use-panel-resize'

export type PanelResizeHandleProps = {
  width: number
  onResize: (width: number) => void
  /** Live width while dragging, so the panel tracks the pointer. */
  onPreview?: (width: number | null) => void
}

/** Arrow-key step for the keyboard path. */
const KEYBOARD_STEP = 16

/**
 * The drag affordance on the panel's left edge. It is a real, focusable
 * `separator` with an `aria-valuenow`, so the panel can be resized without a
 * pointer: Left/Right move the edge by a fixed step, Home/End jump to the
 * bounds. A vertical grip is shown on hover/focus to signal the affordance.
 */
export function PanelResizeHandle({ width, onResize, onPreview }: PanelResizeHandleProps) {
  const commit = useCallback((next: number) => onResize(clampAssistantWidth(next)), [onResize])
  const { dragging, preview, start } = usePanelResize({
    width,
    onCommit: commit,
    onLiveWidth: onPreview,
  })

  const onKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    const current = preview ?? width
    const steps: Record<string, number> = {
      // Right-anchored: ArrowLeft widens, ArrowRight narrows.
      ArrowLeft: current + KEYBOARD_STEP,
      ArrowRight: current - KEYBOARD_STEP,
      Home: ASSISTANT_MIN_WIDTH,
      End: ASSISTANT_MAX_WIDTH,
    }
    const next = steps[event.key]
    if (next === undefined) return
    event.preventDefault()
    commit(next)
  }

  return (
    <div
      role='separator'
      aria-orientation='vertical'
      aria-label='Resize assistant panel'
      aria-valuenow={preview ?? width}
      aria-valuemin={ASSISTANT_MIN_WIDTH}
      aria-valuemax={ASSISTANT_MAX_WIDTH}
      tabIndex={0}
      data-dragging={dragging ? 'true' : undefined}
      onPointerDown={start}
      onKeyDown={onKeyDown}
      className={cn(
        // Sits fully inside the panel: the wrapper clips overflow for the slide
        // animation, so a handle straddling the edge would be unclickable. The
        // hit area is wider than the visible grip for easier grabbing.
        'group absolute inset-y-0 left-0 z-10 w-2 cursor-col-resize',
        'touch-none outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
        dragging && 'bg-ring/30'
      )}
    >
      <span
        aria-hidden='true'
        className={cn(
          'absolute inset-y-0 left-0 w-px bg-border transition-colors',
          'group-hover:bg-ring group-focus-visible:bg-ring',
          dragging && 'bg-ring'
        )}
      />
    </div>
  )
}
