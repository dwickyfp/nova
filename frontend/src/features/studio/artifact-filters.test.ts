import { describe, expect, it } from "vitest";
import {
  filterArtifactRows,
  inferArtifactColumnType,
  type ArtifactFilter,
} from "./artifact-filters";

const filter = (
  column: string,
  operator: ArtifactFilter["operator"],
  value = "",
  secondValue = "",
): ArtifactFilter => ({
  id: `${column}-${operator}`,
  column,
  operator,
  value,
  secondValue,
});

describe("artifact filters", () => {
  const columns = ["channel", "revenue", "created_at"];
  const rows = [
    ["Website", "120.50", "2026-09-01"],
    ["Store", "85.00", "2026-09-02"],
    [null, "200.00", "2026-09-03"],
  ];

  it("infers numeric strings and dates from refreshed query results", () => {
    expect(inferArtifactColumnType(rows, 0)).toBe("text");
    expect(inferArtifactColumnType(rows, 1)).toBe("number");
    expect(inferArtifactColumnType(rows, 2)).toBe("date");
  });

  it("combines typed rules with all or any semantics", () => {
    const rules = [
      filter("revenue", "greater_than", "100"),
      filter("channel", "contains", "web"),
    ];
    expect(filterArtifactRows(columns, rows, rules, "all")).toEqual([rows[0]]);
    expect(filterArtifactRows(columns, rows, rules, "any")).toEqual([
      rows[0],
      rows[2],
    ]);
  });

  it("supports inclusive ranges, dates, and empty cells", () => {
    expect(
      filterArtifactRows(
        columns,
        rows,
        [filter("revenue", "between", "85", "120.5")],
        "all",
      ),
    ).toEqual([rows[0], rows[1]]);
    expect(
      filterArtifactRows(
        columns,
        rows,
        [filter("created_at", "greater_or_equal", "2026-09-02")],
        "all",
      ),
    ).toEqual([rows[1], rows[2]]);
    expect(
      filterArtifactRows(columns, rows, [filter("channel", "is_empty")], "all"),
    ).toEqual([rows[2]]);
  });

  it("keeps incomplete and unavailable rules from hiding data", () => {
    expect(
      filterArtifactRows(
        columns,
        rows,
        [filter("revenue", "between", "100")],
        "all",
      ),
    ).toEqual(rows);
    expect(
      filterArtifactRows(
        columns,
        rows,
        [filter("missing", "equals", "x")],
        "all",
      ),
    ).toEqual(rows);
  });
});
