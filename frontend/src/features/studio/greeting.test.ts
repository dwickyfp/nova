import { describe, expect, it } from "vitest";

import { daypartAt, hourIn } from "./greeting";

describe("greeting daypart", () => {
  it.each([
    [0, "evening"],
    [4, "evening"],
    [5, "morning"],
    [11, "morning"],
    [12, "afternoon"],
    [17, "afternoon"],
    [18, "evening"],
    [23, "evening"],
  ] as const)("hour %i reads as %s", (hour, daypart) => {
    expect(daypartAt(hour)).toBe(daypart);
  });

  it("reads the hour in the client's zone, not UTC", () => {
    // 2026-11-09 01:30 UTC
    const instant = new Date(Date.UTC(2026, 10, 9, 1, 30));
    expect(hourIn(instant, "Asia/Jakarta")).toBe(8);
    expect(daypartAt(hourIn(instant, "Asia/Jakarta"))).toBe("morning");
    expect(hourIn(instant, "Asia/Jayapura")).toBe(10);
    expect(hourIn(instant, "Europe/London")).toBe(1);
    expect(daypartAt(hourIn(instant, "Europe/London"))).toBe("evening");
    expect(hourIn(instant, "America/Los_Angeles")).toBe(17);
    expect(daypartAt(hourIn(instant, "America/Los_Angeles"))).toBe("afternoon");
  });

  it("falls back to the runtime hour for an unknown zone", () => {
    const instant = new Date(2026, 10, 9, 14, 0);
    expect(hourIn(instant, "Not/AZone")).toBe(14);
  });
});
