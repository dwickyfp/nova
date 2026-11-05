/** Superset's own spinner, shown while a page's first charts query. */
export const SUPERSET_LOADING = '[aria-label="Loading"]'
/** Past this, the frame is shown even if a chart is still querying. */
export const FIRST_DRAW_LIMIT_MS = 10_000
export const FIRST_DRAW_POLL_MS = 150

/** True while the framed Superset page still shows its own loading state. */
export function stillDrawing(frame: HTMLIFrameElement | null): boolean {
  try {
    return Boolean(frame?.contentDocument?.querySelector(SUPERSET_LOADING))
  } catch {
    return false
  }
}
