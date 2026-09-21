import { getTimezone } from "@/stores/timezone-store";

/**
 * Date formatting pinned to the deployment timezone.
 *
 * Every timestamp Nova displays goes through here. Rendering in the browser's
 * local zone would show each user a different clock and could disagree with the
 * value StarRocks wrote for `NOW()`; both are wrong on a multi-region team. The
 * zone comes from `GET /system/info` (NOVA_TIMEZONE), defaulting to
 * `Asia/Jakarta`, and is applied via `Intl`'s `timeZone` option.
 */

const DEFAULT_OPTIONS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
};

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Format an instant in the deployment timezone. Returns `fallback` if invalid. */
export function formatDateTime(
  value: string | number | Date | null | undefined,
  options: Intl.DateTimeFormatOptions = DEFAULT_OPTIONS,
  fallback = "—",
): string {
  const date = toDate(value);
  if (!date) return fallback;
  return new Intl.DateTimeFormat("en-US", {
    ...options,
    timeZone: getTimezone(),
  }).format(date);
}

/** Date only (no time), in the deployment timezone. */
export function formatDate(
  value: string | number | Date | null | undefined,
  options: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "short",
    day: "numeric",
  },
  fallback = "—",
): string {
  return formatDateTime(value, options, fallback);
}

/** Time only (no date), in the deployment timezone. */
export function formatTime(
  value: string | number | Date | null | undefined,
  options: Intl.DateTimeFormatOptions = {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  },
  fallback = "—",
): string {
  return formatDateTime(value, options, fallback);
}
