import { describe, expect, it } from "vitest";
import { isSqlPreview } from "./tool-preview";

describe("isSqlPreview", () => {
  it("recognises SQL statements", () => {
    expect(isSqlPreview("SELECT 1")).toBe(true);
    expect(isSqlPreview("  -- note\nWITH t AS (SELECT 1) SELECT * FROM t")).toBe(true);
    expect(isSqlPreview("CALL sys.refresh('t')")).toBe(true);
  });

  it("keeps sentences that start with Call as prose", () => {
    expect(isSqlPreview('Call external tool fx_convert on Nova test tools with {"amount": 250}')).toBe(false);
    expect(isSqlPreview("External tool fx_convert on Nova test tools")).toBe(false);
  });
});
