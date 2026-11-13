import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api-client";
import { queryErrorMessages, refusedQueryResults } from "./query-messages";
import type { QueryResponse } from "./types";

const refused: QueryResponse = {
  success: false,
  columns: [],
  rows: [],
  row_count: 0,
  affected_rows: 0,
  elapsed_ms: 0,
  original_sql: "SELECT * FROM t INTO OUTFILE 's3://x/y'",
  executed_sql: "",
  warnings: [
    "INTO OUTFILE exports query results to a file and is not read-only.",
  ],
  error: "INTO OUTFILE exports query results to a file and is not read-only.",
  destructive: false,
  needs_confirmation: false,
};

describe("queryErrorMessages", () => {
  it("shows an error repeated in the warnings once", () => {
    expect(
      queryErrorMessages({
        error: "Unknown table 'db.t'",
        warnings: ["Unknown table 'db.t'"],
      }),
    ).toEqual(["Unknown table 'db.t'"]);
  });

  it("keeps distinct warnings after the error", () => {
    expect(
      queryErrorMessages({ error: "Failed", warnings: ["Failed", "Hint"] }),
    ).toEqual(["Failed", "Hint"]);
  });

  it("falls back when nothing was reported", () => {
    expect(queryErrorMessages({ error: null, warnings: ["  "] })).toEqual([
      "Query failed.",
    ]);
  });
});

describe("refusedQueryResults", () => {
  it("returns the statement results carried by a refusal", () => {
    const error = new ApiError(403, "Request failed", [refused]);
    expect(refusedQueryResults(error)).toEqual([refused]);
  });

  it("ignores errors without statement results", () => {
    expect(refusedQueryResults(new Error("Network down"))).toBeNull();
    expect(
      refusedQueryResults(new ApiError(403, "Denied", { detail: "Denied" })),
    ).toBeNull();
    expect(
      refusedQueryResults(new ApiError(500, "Oops", [{ unexpected: true }])),
    ).toBeNull();
  });
});
