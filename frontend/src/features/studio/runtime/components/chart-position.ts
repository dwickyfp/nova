/** The chart's position on its page, read from its signed draft reference. */
export function chartPosition(ref: unknown): number | null {
  if (typeof ref !== "string") return null;
  const [, payload] = ref.split(".");
  if (!payload) return null;
  try {
    const padded = payload.replace(/-/g, "+").replace(/_/g, "/");
    const value = JSON.parse(atob(padded + "=".repeat((4 - (padded.length % 4)) % 4))) as {
      n?: unknown;
    };
    return typeof value.n === "number" && Number.isInteger(value.n) ? value.n : null;
  } catch {
    return null;
  }
}
