import type { Story } from "./newspaper-api";

export type ChartKind = "trend" | "weekday" | "level" | "drivers";

/**
 * Which chart tells a story best on the edition page. A whole-view story shows
 * where it moved; slice stories rotate so neighbours do not repeat one form.
 */
export function chartKind(story: Story, position: number): ChartKind {
  if (position === 0) return "trend";
  if (story.drivers.length) return "drivers";
  // The head story is a trend, so the story after it starts on another form.
  return (["weekday", "trend", "level"] as const)[(position - 1) % 3];
}
