import type { StatusTone } from './status-badge'

/**
 * The standard colour of a lifecycle state, so every screen shows the same
 * state the same way: a good, live state is green, one that needs attention
 * is amber, a failed or lost one is red, and a retired one is grey. Unknown
 * states stay neutral rather than borrowing a meaning.
 */
const TONES: Record<string, StatusTone> = {
  active: 'success',
  available: 'success',
  completed: 'success',
  connected: 'success',
  enabled: 'success',
  granted: 'success',
  healthy: 'success',
  matched: 'success',
  published: 'success',
  ready: 'success',
  succeeded: 'success',
  success: 'success',
  verified: 'success',
  draft: 'warning',
  'needs verification': 'warning',
  pending: 'warning',
  review: 'warning',
  stale: 'warning',
  error: 'danger',
  failed: 'danger',
  rejected: 'danger',
  revoked: 'danger',
  unavailable: 'danger',
  running: 'info',
  archived: 'neutral',
  deprecated: 'neutral',
  disabled: 'neutral',
  inactive: 'neutral',
}

function key(status: string): string {
  return status.trim().toLowerCase().replace(/[_-]+/g, ' ')
}

export function statusTone(status: string | null | undefined): StatusTone {
  return status ? (TONES[key(status)] ?? 'neutral') : 'neutral'
}

/** `NEEDS_VERIFICATION` → `Needs verification`. */
export function statusLabel(status: string): string {
  const text = key(status)
  return text.charAt(0).toUpperCase() + text.slice(1)
}
