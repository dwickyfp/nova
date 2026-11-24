import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'

/** A position on the canvas: across as 0–1 of its width, down in grid rows. */
export type CanvasPoint = { x: number; y: number }

export type Collaborator = {
  viewer: string
  name: string
  page_id: string | null
  cursor: CanvasPoint | null
  editing: boolean
  seconds_ago: number
}

type Beat = { viewer: string; others: Collaborator[]; more: number }

/** How often this viewer says it is here; others see a pointer move this often. */
export const BEAT_MS = 1000
/** A legacy grid row in page pixels; pointers are shared in these rows down the page. */
export const ROW_PX = 72

// Theme series tokens, so each person keeps one colour on every screen.
const COLOURS = [1, 2, 3, 4, 5, 6, 7, 8, 9].map(index => `var(--chart-${index})`)

export function colourFor(viewer: string): string {
  let hash = 0
  for (const char of viewer) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  return COLOURS[hash % COLOURS.length]
}

export function initials(name: string): string {
  const parts = name.replace(/[._-]+/g, ' ').trim().split(/\s+/).filter(Boolean)
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : (parts[0] ?? '?').slice(0, 2)).toUpperCase()
}

/**
 * Who else has this dashboard open. A heartbeat each second carries this
 * viewer's page, pointer and whether they are editing; the reply lists the
 * others. Hidden tabs stop sending and drop off after a few seconds.
 */
export function usePresence({ dashboardId, pageId, editing }: { dashboardId: string; pageId: string | null; editing: boolean }) {
  const epoch = useAuthStore(state => state.securityEpoch)
  const [others, setOthers] = useState<Collaborator[]>([])
  const [more, setMore] = useState(0)
  const pointer = useRef<CanvasPoint | null>(null)
  const state = useRef({ pageId, editing })
  state.current = { pageId, editing }

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const path = `/viz/dashboards/${encodeURIComponent(dashboardId)}/presence`
    const beat = async () => {
      if (stopped) return
      if (document.visibilityState === 'visible') {
        try {
          const reply = await api.request<Beat>(path, {
            method: 'PUT', securityEpoch: epoch,
            body: JSON.stringify({ page_id: state.current.pageId, cursor: pointer.current, editing: state.current.editing }),
          })
          if (!stopped && reply) {
            setOthers(reply.others)
            setMore(reply.more)
          }
        } catch {
          // Presence is a courtesy: a failed beat hides others until the next one.
          if (!stopped) { setOthers([]); setMore(0) }
        }
      }
      if (!stopped) timer = setTimeout(beat, BEAT_MS)
    }
    void beat()
    return () => {
      stopped = true
      clearTimeout(timer)
      setOthers([])
      // Leave at once instead of waiting for the entry to expire.
      void api.request(path, { method: 'DELETE', body: '{}', keepalive: true, securityEpoch: epoch }).catch(() => undefined)
    }
  }, [dashboardId, epoch])

  const point = useCallback((value: CanvasPoint | null) => { pointer.current = value }, [])
  return { others, more, point }
}
