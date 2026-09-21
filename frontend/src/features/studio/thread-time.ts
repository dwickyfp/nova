/**
 * When a conversation last changed, as a short relative date.
 *
 * Recency is the field you scan the history list for, and every title in the
 * list is a question that repeats. The clock time is only useful within the
 * current day, so it is shown for today and dropped for anything older.
 */
export function relativeUpdatedAt(iso: string): string {
  const updated = new Date(iso);
  if (Number.isNaN(updated.getTime())) return "";
  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  ).getTime();
  const elapsed = now.getTime() - updated.getTime();

  if (updated.getTime() >= startOfToday) {
    if (elapsed < 60_000) return "now";
    if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}m`;
    return updated.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    });
  }
  if (updated.getTime() >= startOfToday - 86_400_000) return "Yesterday";
  return updated.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}
