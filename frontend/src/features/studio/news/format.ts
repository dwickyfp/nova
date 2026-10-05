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

/** Critical first, then the order the edition ranked them. */
export function byPriority(a: Story, b: Story): number {
  return (
    Number(b.severity === "critical") - Number(a.severity === "critical") ||
    a.rank - b.rank
  );
}
