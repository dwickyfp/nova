import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { SqlDisclosure } from "./thought-turn";

describe("SQL disclosure", () => {
  const preview =
    "select `visit_route_features`.`track_date` as `route_day`, count(distinct `visit_route_features`.`feature_id`) as `map_features` from `NOVA_FIELD_SALES`.`visit_route_features` where `label` = 'Depo' order by `route_day` asc";

  it("formats and highlights the statement for reading", async () => {
    const { container } = await render(<SqlDisclosure preview={preview} />);
    const code = (container as HTMLElement).querySelector("code");

    expect(code?.querySelectorAll(".hljs-keyword").length).toBeGreaterThan(0);
    expect(code?.textContent?.startsWith("SELECT")).toBe(true);
    expect(code?.textContent).toContain("\n");
    expect(code?.textContent).toContain("ORDER BY");
  });

  it("copies the statement as it ran, not the formatted reading view", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    // The browser harness has no clipboard by default; stub it. `clipboard` is
    // a getter on Navigator, so it must be redefined rather than assigned.
    vi.spyOn(navigator, "clipboard", "get").mockReturnValue({
      writeText,
    } as unknown as Clipboard);
    const { getByRole } = await render(<SqlDisclosure preview={preview} />);

    await getByRole("button", { name: "Copy SQL" }).click();
    expect(writeText).toHaveBeenCalledWith(preview);
  });
});
