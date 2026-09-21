import { afterEach, describe, expect, it } from "vitest";
import { formatDate, formatDateTime, formatTime } from "./datetime";
import { DEFAULT_TIMEZONE, useTimezoneStore } from "@/stores/timezone-store";

const setZone = (zone: string) => useTimezoneStore.getState().setTimezone(zone);

afterEach(() => setZone(DEFAULT_TIMEZONE));

describe("formatDateTime", () => {
  it("renders in the deployment timezone, not the host zone", () => {
    // 02:00 UTC is 09:00 in Asia/Jakarta (UTC+7, no DST).
    setZone("Asia/Jakarta");
    expect(
      formatDateTime("2026-01-01T02:00:00Z", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }),
    ).toBe("09:00");
  });

  it("honours a different deployment timezone", () => {
    // 02:00 UTC is 03:00 in Europe/Berlin (UTC+1 in January).
    setZone("Europe/Berlin");
    expect(
      formatDateTime("2026-01-01T02:00:00Z", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }),
    ).toBe("03:00");
  });

  it("returns the fallback for null, undefined, and empty input", () => {
    expect(formatDateTime(null, undefined, "—")).toBe("—");
    expect(formatDateTime(undefined, undefined, "—")).toBe("—");
    expect(formatDateTime("", undefined, "—")).toBe("—");
  });

  it("returns the fallback for an unparseable value", () => {
    expect(formatDateTime("not-a-date", undefined, "—")).toBe("—");
  });
});

describe("formatDate and formatTime", () => {
  it("formats a date in the deployment timezone", () => {
    setZone("Asia/Jakarta");
    // 17:00 UTC is already the next day in Jakarta.
    expect(
      formatDate("2026-01-01T17:00:00Z", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }),
    ).toBe("01/02/2026");
  });

  it("formats a time in the deployment timezone", () => {
    setZone("Asia/Jakarta");
    expect(
      formatTime("2026-01-01T02:00:00Z", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }),
    ).toBe("09:00");
  });
});
