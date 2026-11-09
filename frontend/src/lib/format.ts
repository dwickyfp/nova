import { formatDateTime as formatZonedDateTime } from '@/lib/datetime'

/**
 * Display formatting shared by the monitoring and cluster screens, so a
 * duration, a count or a timestamp reads the same on every page. Timestamps
 * go through `@/lib/datetime` and stay pinned to the deployment timezone.
 */

const EMPTY = '—'

/** `75` → `1m 15s`, `3720` → `1h 2m`. Expects at least one minute. */
function formatLongSeconds(totalSeconds: number): string {
  const rounded = Math.round(totalSeconds)
  const minutes = Math.floor(rounded / 60)
  const seconds = rounded % 60
  if (minutes < 60) return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`

  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`
}

/** A duration in milliseconds: `850ms`, `2.35s`, `1m 15s`, `1h 2m`. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return EMPTY
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`
  return formatLongSeconds(ms / 1000)
}

/** A duration in whole seconds: `45s`, `2m 5s`, `1h 2m`. */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return EMPTY
  if (seconds < 60) return `${Math.round(seconds)}s`
  return formatLongSeconds(seconds)
}

/** `Mar 4, 09:15 AM`, or with `seconds` `Mar 4, 09:15:07 AM`. */
export function formatDateTime(
  value: string | number | Date | null | undefined,
  { seconds = false }: { seconds?: boolean } = {}
): string {
  return formatZonedDateTime(
    value,
    {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      ...(seconds ? { second: '2-digit' } : {}),
    },
    EMPTY
  )
}

/** Cuts long text at `max` characters and marks the cut. */
export function truncate(value: string, max = 80): string {
  if (value.length <= max) return value
  return `${value.slice(0, max).trimEnd()}...`
}

/** `12,345`, or with `compact` `12.3K` / `4.5M`. */
export function formatNumber(
  value: number | null | undefined,
  { compact = false }: { compact?: boolean } = {}
): string {
  if (value == null || !Number.isFinite(value)) return EMPTY
  if (compact && Math.abs(value) >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)}M`
  }
  if (compact && Math.abs(value) >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`
  }
  return value.toLocaleString()
}
