import * as React from 'react'

const NARROW_QUERY = '(max-width: 1023px)'

/**
 * The assistant panel is inline above 1024px and a `Sheet` overlay below it.
 * A right panel needs the editor to keep a workable width, so the switch point
 * is higher than the app's 768px mobile breakpoint.
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
