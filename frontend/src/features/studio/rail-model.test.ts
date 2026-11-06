import { expect, it } from "vitest";
import { formatSpan, isFailedStatus, isLiveStatus, recordedSpan } from "./rail-model";

it("tells live and failed rows apart", () => {
  expect(isLiveStatus("running")).toBe(true);
  expect(isLiveStatus("pending")).toBe(true);
  expect(isLiveStatus("done")).toBe(false);
  expect(isFailedStatus("failed")).toBe(true);
  expect(isFailedStatus("denied")).toBe(true);
  expect(isFailedStatus("cancelled")).toBe(true);
  expect(isFailedStatus("done")).toBe(false);
});

it("formats spans for the rail", () => {
  expect(formatSpan(4730)).toBe("4.7s");
  expect(formatSpan(65_000)).toBe("1m 05s");
  expect(recordedSpan("2026-09-25T00:00:01", "2026-09-25T00:00:07")).toBe(6000);
  expect(recordedSpan("2026-09-25T00:00:07", "2026-09-25T00:00:01")).toBeUndefined();
  expect(recordedSpan(undefined, "2026-09-25T00:00:01")).toBeUndefined();
});
