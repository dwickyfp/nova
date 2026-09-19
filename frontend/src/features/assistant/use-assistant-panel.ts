import * as React from 'react'

const NARROW_QUERY = '(max-width: 767px)'

/**
 * The assistant panel is inline from `md` (768px) up, matching the sidebar's
 * own breakpoint, and a `Sheet` overlay below it. Keeping the two ladders
 * aligned means a viewport can never fall into a gap where the sidebar is
 * inline but the panel has no surface.
 */
export function useIsNarrowForAssistant() {
  return React.useSyncExternalStore(
    (callback) => {
      const mql = window.matchMedia(NARROW_QUERY)
      mql.addEventListener('change', callback)
      return () => mql.removeEventListener('change', callback)
    },
    () => window.matchMedia(NARROW_QUERY).matches,
    () => false
  )
}
