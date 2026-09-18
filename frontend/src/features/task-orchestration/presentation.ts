import type { StatusTone } from '@/components/ui/status-badge';
import type { GraphRunState, ScheduleKind, TaskRunState } from './api';

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatDuration(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
): string {
  if (!startedAt || !finishedAt) return '—';
  const durationMs =
    new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (Number.isNaN(durationMs) || durationMs < 0) return '—';
  if (durationMs < 1000) return `${durationMs}ms`;

  const seconds = durationMs / 1000;
  if (seconds < 60) return `${seconds.toFixed(2)}s`;

  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  if (minutes < 60) {
    return remainingSeconds > 0
      ? `${minutes}m ${remainingSeconds}s`
      : `${minutes}m`;
  }

  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

const SUCCESS_STATES = new Set(['success']);
const ACTIVE_STATES = new Set(['running']);
const FAILED_STATES = new Set(['failed']);
const NEUTRAL_STATES = new Set(['cancelled', 'skipped']);

export function graphRunTone(state: GraphRunState): StatusTone {
  if (SUCCESS_STATES.has(state)) return 'success';
  if (ACTIVE_STATES.has(state)) return 'info';
  if (FAILED_STATES.has(state)) return 'danger';
  if (NEUTRAL_STATES.has(state)) return 'neutral';
  return 'warning';
}

export function taskRunTone(state: TaskRunState): StatusTone {
  if (SUCCESS_STATES.has(state)) return 'success';
  if (ACTIVE_STATES.has(state)) return 'info';
  if (FAILED_STATES.has(state)) return 'danger';
  if (state === 'abandoned') return 'warning';
  if (NEUTRAL_STATES.has(state)) return 'neutral';
  return 'warning';
}

export const SCHEDULE_KIND_LABELS: Record<ScheduleKind, string> = {
  manual: 'Manual',
  interval: 'Interval',
  cron: 'Cron',
};

export function formatSchedule(
  kind: ScheduleKind | null | undefined,
  expr: string | null | undefined,
): string {
  if (!kind) return '—';
  const label = SCHEDULE_KIND_LABELS[kind] ?? kind;
  return expr ? `${label} · ${expr}` : label;
}

const ACCESS_ERROR_CODES = [
  '404',
  '403',
  'not found',
  'no access',
  'forbidden',
];

/**
 * The backend answers the same `404` for an unknown graph/run and for one the
 * caller may not read, deliberately. The UI renders both as 'not found / no
 * access' rather than an error page, and never infers access from its own
 * state; the backend is the boundary.
 */
export function isAccessError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '');
  const normalized = message.toLowerCase();
  return ACCESS_ERROR_CODES.some((marker) => normalized.includes(marker));
}
