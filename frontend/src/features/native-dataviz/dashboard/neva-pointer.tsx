import { useLayoutEffect, useState, type RefObject } from 'react'
import { CircleAlert, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { NevaAction, NevaState } from './events'

/** A change the Dataviz assistant makes on this dashboard and how far it got. */
export type NevaActivity = {
  tool_call_id: string
  action: NevaAction
  widget_ids: string[]
  page_id?: string
  state: NevaState
}

const DOING: Record<NevaAction, string> = {
  add: 'add a chart', change: 'change', move: 'move', remove: 'remove', arrange: 'rearrange',
  filter: 'change the filters', data: 'add data', publish: 'publish the dashboard',
}

const SHORT: Record<NevaState, string> = {
  saving: 'Saving…', saved: 'Saved', failed: 'Not saved', stopped: 'Stopped',
}

/** What the assistant is doing, as one sentence for screen readers and a short chip label. */
export function nevaWords(activity: NevaActivity, titleOf: (widgetId: string) => string | undefined) {
  const names = activity.widget_ids.map(id => titleOf(id)).filter((name): name is string => !!name)
  const target = ['change', 'move', 'remove', 'arrange'].includes(activity.action)
    ? `${DOING[activity.action]} ${names.length ? names.slice(0, 3).map(name => `“${name}”`).join(', ') + (names.length > 3 ? ` and ${names.length - 3} more` : '') : 'a widget'}`
    : DOING[activity.action]
  const sentence = {
    saving: `The assistant is saving: ${target}.`,
    saved: `The assistant saved its change: ${target}.`,
    failed: `The assistant could not save its change (${target}). Nothing was changed.`,
    stopped: `The assistant's change was not made (${target}). Nothing was changed.`,
  }[activity.state]
  return { sentence, short: SHORT[activity.state] }
}

function reducedMotion() {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

/**
 * The assistant's pointer on the canvas, at the widget its change touches.
 * Decorative and never focusable: the editor announces the same state in a
 * status region, so typing and focus stay with the person.
 */
export function NevaPointer({ activity, host, short }: { activity: NevaActivity; host: RefObject<HTMLDivElement | null>; short: string }) {
  const [spot, setSpot] = useState<{ left: number; top: number } | null>(null)
  const [arrived, setArrived] = useState(false)
  const key = activity.widget_ids.join(',')
  useLayoutEffect(() => {
    const grid = host.current
    const frame = grid?.parentElement
    if (!grid || !frame) return
    const place = () => {
      const box = frame.getBoundingClientRect()
      const element = activity.widget_ids
        .map(id => grid.querySelector<HTMLElement>(`[data-element-id="${CSS.escape(id)}"]`))
        .find((item): item is HTMLElement => !!item)
      if (element) {
        // On the widget's top edge, at the right, clear of its title.
        const rect = element.getBoundingClientRect()
        setSpot({ left: rect.right - box.left - 12, top: rect.top - box.top + 6 })
      } else {
        // A new widget lands below what is there.
        setSpot({ left: Math.min(grid.clientWidth, 260), top: Math.max(12, grid.scrollHeight - 4) })
      }
    }
    place()
    const observer = new ResizeObserver(place)
    observer.observe(grid)
    return () => observer.disconnect()
  }, [host, key])
  // Glide in from the chat side, unless motion is reduced: then it appears in place.
  useLayoutEffect(() => {
    if (reducedMotion()) { setArrived(true); return }
    setArrived(false)
    const id = requestAnimationFrame(() => requestAnimationFrame(() => setArrived(true)))
    return () => cancelAnimationFrame(id)
  }, [activity.tool_call_id])
  if (!spot) return null
  const failed = activity.state === 'failed' || activity.state === 'stopped'
  return <div aria-hidden data-neva-pointer={activity.state} data-arrived={arrived}
    className='pointer-events-none absolute z-30 flex -translate-x-full -translate-y-1/2 items-center gap-0.5 select-none motion-safe:transition-[left,top,opacity] motion-safe:duration-700 motion-safe:ease-out'
    style={arrived ? { left: spot.left, top: spot.top } : { left: '100%', top: spot.top, opacity: 0 }}>
    <span className={cn('flex max-w-56 items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] leading-4 font-medium shadow-sm',
      failed ? 'border border-border bg-background text-foreground' : 'bg-brand text-brand-foreground')}>
      {failed ? <CircleAlert className='size-3 shrink-0 text-destructive' aria-hidden /> : <Sparkles className='size-3 shrink-0' aria-hidden />}
      <span className='truncate'>Assistant · {short}</span>
    </span>
    <svg viewBox='0 0 16 16' className='size-4 shrink-0 -rotate-90 drop-shadow-sm' aria-hidden>
      <path d='M2 1.5 13.5 7 8 8.5 5.5 14z' className={failed ? 'fill-muted-foreground' : 'fill-brand'} stroke='var(--background)' strokeWidth='1.2' strokeLinejoin='round' />
    </svg>
  </div>
}
