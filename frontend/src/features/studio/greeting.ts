export type Daypart = "morning" | "afternoon" | "evening";

/** The browser's own zone, which is where the person reading the greeting is. */
export function clientTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

/** Wall-clock hour (0-23) at `date` in `timeZone`; the runtime's zone when absent or unknown. */
export function hourIn(date: Date, timeZone?: string): number {
  try {
    const hour = new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      hourCycle: "h23",
      timeZone,
    })
      .formatToParts(date)
      .find((part) => part.type === "hour")?.value;
    const parsed = Number(hour);
    if (Number.isInteger(parsed) && parsed >= 0 && parsed < 24) return parsed;
  } catch {
    // An unknown zone falls through to the runtime's local hour.
  }
  return date.getHours();
}

/**
 * 05:00-11:59 morning, 12:00-17:59 afternoon, otherwise evening. Nobody is
 * wished a good morning at 2 a.m.
 */
export function daypartAt(hour: number): Daypart {
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 18) return "afternoon";
  return "evening";
}

export const GREETING: Record<Daypart, string> = {
  morning: "Good morning",
  afternoon: "Good afternoon",
  evening: "Good evening",
};
