import type { Story } from "./newspaper-api";

const CURRENCY = /^[A-Z]{3}$/;

/** A figure with its unit; currency codes lead, every other unit follows. */
export function formatValue(
  value: number,
  unit: string | null,
  compact = false,
): string {
  const text = new Intl.NumberFormat("en", {
    notation: compact ? "compact" : "standard",
    maximumFractionDigits: compact ? 1 : Math.abs(value) >= 100 ? 0 : 2,
  }).format(value);
  if (!unit) return text;
  return CURRENCY.test(unit) ? `${unit} ${text}` : `${text} ${unit}`;
}

export function formatChange(relative: number | null): string | null {
  if (relative == null) return null;
  return `${relative > 0 ? "+" : "−"}${(Math.abs(relative) * 100).toFixed(1)}%`;
}

/** Dates arrive as calendar days; parse them without a timezone shift. */
export function calendarDay(value: string): Date {
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  return new Date(year, month - 1, day);
}

export function longDate(value: string): string {
  const date = calendarDay(value);
  const part = (options: Intl.DateTimeFormatOptions) =>
    date.toLocaleDateString("en", options);
  return `${part({ weekday: "long" })}, ${date.getDate()} ${part({ month: "long" })} ${date.getFullYear()}`;
}

export function humanize(name: string): string {
  const text = name.split("_").join(" ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function kicker(story: Story): string {
  return story.slice
    ? `${humanize(story.slice.dimension)} · ${story.slice.value}`
    : "Whole view";
}

/** The reader's own order from the server; the edition's order when absent. */
export function byPriority(a: Story, b: Story): number {
  if (a.score != null && b.score != null && a.score !== b.score)
    return b.score - a.score;
  return (
    Number(b.severity === "critical") - Number(a.severity === "critical") ||
    a.rank - b.rank
  );
}

export type Tone = "good" | "bad" | "neutral";

/**
 * Whether the change is good for the business. The model judges each metric
 * once; when it has not, a rise reads as good and a fall as bad.
 */
export function tone(story: Story): Tone {
  if (story.impact === "favorable") return "good";
  if (story.impact === "unfavorable") return "bad";
  if (story.impact === "neutral") return "neutral";
  return story.change > 0 ? "good" : "bad";
}

export const TONE_TEXT: Record<Tone, string> = {
  good: "text-success-strong",
  bad: "text-destructive",
  neutral: "text-foreground",
};

/** "−34.6% (−IDR 134.6M)": the relative and absolute change together. */
export function deltaLine(story: Story): string {
  const sign = story.change > 0 ? "+" : "−";
  const amount = `${sign}${formatValue(Math.abs(story.change), story.unit, true)}`;
  const relative = formatChange(story.relative_change);
  return relative ? `${relative} (${amount})` : amount;
}

export function weekdayName(story: Story): string {
  return calendarDay(story.edition_date).toLocaleDateString("en", { weekday: "long" });
}
