import { describe, expect, it } from "vitest";
import {
  formatDuration,
  formatSchedule,
  formatTimestamp,
  graphRunTone,
  isAccessError,
  taskRunTone,
} from "./presentation";

describe("graphRunTone", () => {
  it("maps a successful run to the success tone", () => {
    expect(graphRunTone("success")).toBe("success");
  });

  it("maps a failed run to the danger tone, not the neutral one", () => {
    expect(graphRunTone("failed")).toBe("danger");
  });

  it("treats a running run as in-progress", () => {
    expect(graphRunTone("running")).toBe("info");
  });

  it("treats a cancelled run as neutral", () => {
    expect(graphRunTone("cancelled")).toBe("neutral");
  });

  it("treats a pending run as a warning", () => {
    expect(graphRunTone("pending")).toBe("warning");
  });
});

describe("taskRunTone", () => {
  it("maps a skipped node to neutral, not failure", () => {
    expect(taskRunTone("skipped")).toBe("neutral");
  });

  it("maps an abandoned node to warning", () => {
    expect(taskRunTone("abandoned")).toBe("warning");
  });

  it("maps a failed node to danger", () => {
    expect(taskRunTone("failed")).toBe("danger");
  });
});

describe("formatDuration", () => {
  it("returns a dash when the run has not finished", () => {
    expect(formatDuration("2026-09-18T03:00:00Z", null)).toBe("—");
  });

  it("formats sub-second durations in milliseconds", () => {
    expect(
      formatDuration("2026-09-18T03:00:00.000Z", "2026-09-18T03:00:00.250Z"),
    ).toBe("250ms");
  });

  it("formats durations over a minute with minutes and seconds", () => {
    expect(formatDuration("2026-09-18T03:00:00Z", "2026-09-18T03:02:05Z")).toBe(
      "2m 5s",
    );
  });

  it("returns a dash for an unparseable timestamp instead of NaN", () => {
    expect(formatDuration("not-a-date", "2026-09-18T03:00:00Z")).toBe("—");
  });
});

describe("formatTimestamp", () => {
  it("returns a dash for null", () => {
    expect(formatTimestamp(null)).toBe("—");
  });

  it("returns a dash for an unparseable value", () => {
    expect(formatTimestamp("nope")).toBe("—");
  });
});

describe("formatSchedule", () => {
  it("returns a dash when there is no schedule kind", () => {
    expect(formatSchedule(null, null)).toBe("—");
  });

  it("labels a manual schedule without an expression", () => {
    expect(formatSchedule("manual", null)).toBe("Manual");
  });

  it("appends the expression when present", () => {
    expect(formatSchedule("cron", "0 3 * * *")).toBe("Cron · 0 3 * * *");
  });
});

describe("isAccessError", () => {
  // The backend returns the same 404 for unknown and unauthorized graphs, so
  // the UI must render both as 'no access' rather than an error page.
  it("recognises a 404 detail message as an access error", () => {
    expect(isAccessError(new Error("Graph 'g1' not found"))).toBe(true);
  });

  it("recognises a 403 message as an access error", () => {
    expect(isAccessError(new Error("403 Forbidden"))).toBe(true);
  });

  it("does not classify a network failure as an access error", () => {
    expect(isAccessError(new Error("Failed to fetch"))).toBe(false);
  });

  it("handles a non-Error rejection without throwing", () => {
    expect(isAccessError("404 not found")).toBe(true);
  });
});
