import type { StatusTone } from '@/components/ui/status-badge'

/**
 * Monitoring screens report state as free-form strings from different backend
 * endpoints. This is the one place that decides which status tone a string
 * means, so six pages cannot disagree about whether "CANCELLED" is a warning
 * or a failure.
 */
const SUCCESS_MARKERS = ['SUCCESS', 'FINISHED', 'DONE', 'OK']
const WARNING_MARKERS = [
  'RUNNING',
  'PENDING',
  'LOADING',
  'IN_PROGRESS',
  'EXEC',
  'SEND',
  'SLEEP',
]

export function statusTone(value: string | null | undefined): StatusTone {
  if (!value) return 'neutral'

  const normalized = value.toUpperCase()

  if (SUCCESS_MARKERS.some((marker) => normalized.includes(marker))) {
    return 'success'
  }

  if (WARNING_MARKERS.some((marker) => normalized.includes(marker))) {
    return 'warning'
  }

  if (
    normalized.includes('FAIL') ||
    normalized.includes('ERROR') ||
    normalized.includes('CANCEL') ||
    normalized.includes('KILL')
  ) {
    return 'danger'
  }

  return 'neutral'
}
