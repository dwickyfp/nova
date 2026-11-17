import { describe, expect, it } from "vitest";
import * as XLSX from "xlsx";

describe("workspace Excel export", () => {
  it("preserves column names, Unicode, exact numeric text and worksheet order", () => {
    const rows = [
      { city: "Jakarta", amount: 12.5, account: "9223372036854775807", note: "日本語" },
      { city: "Bandung", amount: 0, account: "00123", note: "résumé" },
    ];
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(rows), "Sales");
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet([]), "Empty result");
    const bytes = XLSX.write(workbook, { type: "array", bookType: "xlsx" });
    const reopened = XLSX.read(bytes, { type: "array" });
    expect(reopened.SheetNames).toEqual(["Sales", "Empty result"]);
    expect(XLSX.utils.sheet_to_json(reopened.Sheets.Sales)).toEqual(rows);
    expect(XLSX.utils.sheet_to_json(reopened.Sheets["Empty result"])).toEqual([]);
  });

  it("keeps formula-shaped user content as text in the saved workbook", () => {
    const inputs = ["=1+1", "+SUM(1,1)", "-1+2", "@SUM(1,1)", "=HYPERLINK(\"https://example.invalid\")"];
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(
      workbook,
      XLSX.utils.json_to_sheet(inputs.map((text) => ({ text }))),
      "Query results",
    );
    const reopened = XLSX.read(XLSX.write(workbook, { type: "array", bookType: "xlsx" }), {
      type: "array",
    });
    inputs.forEach((value, index) => {
      const cell = reopened.Sheets["Query results"][`A${index + 2}`];
      expect(cell.t).toBe("s");
      expect(cell.v).toBe(value);
      expect(cell.f).toBeUndefined();
      expect(cell.l).toBeUndefined();
    });
  });
});
