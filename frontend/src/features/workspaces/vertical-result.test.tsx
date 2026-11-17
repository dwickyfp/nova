import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { page, userEvent } from "vitest/browser";
import { VerticalResult } from "./vertical-result";
import "@/styles/index.css";

describe("VerticalResult", () => {
  it("shows each record as a block of column names beside values", async () => {
    render(
      <VerticalResult
        columns={["now()", "now(1)", "note"]}
        rows={[
          ["2026-11-16 21:18:53", "2026-11-16 21:18:53.3", null],
          ["2026-11-16 21:18:54", "2026-11-16 21:18:54.1", "second"],
        ]}
        firstRowNumber={101}
      />,
    );
    const first = page.getByRole("region", { name: "Row 101" });
    await expect.element(first).toBeVisible();
    await expect.element(first.getByText("now(1)")).toBeVisible();
    await expect
      .element(first.getByText("2026-11-16 21:18:53.3"))
      .toBeVisible();
    await expect.element(first.getByText("NULL")).toBeVisible();
    await expect
      .element(
        page.getByRole("region", { name: "Row 102" }).getByText("second"),
      )
      .toBeVisible();
  });

  it("reports the absolute row and column of a right-clicked value", async () => {
    const onCellContextMenu = vi.fn();
    render(
      <VerticalResult
        columns={["a", "b"]}
        rows={[[1, "x"]]}
        firstRowNumber={26}
        onCellContextMenu={onCellContextMenu}
      />,
    );
    await userEvent.click(page.getByText("x"), { button: "right" });
    expect(onCellContextMenu).toHaveBeenCalledWith(expect.anything(), 25, 1);
  });
});
