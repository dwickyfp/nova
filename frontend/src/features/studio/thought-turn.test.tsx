import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { page } from "vitest/browser";
import { ProcessRail, SqlDisclosure, ToolPreview, type RailStep } from "./thought-turn";
import { isSqlPreview } from "./tool-preview";

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

describe("tool preview", () => {
  it("highlights only previews that are SQL statements", () => {
    expect(isSqlPreview("SELECT 1")).toBe(true);
    expect(isSqlPreview("  (select 1) union all (select 2)")).toBe(true);
    expect(isSqlPreview("-- revenue\nwith t as (select 1) select * from t")).toBe(true);
    expect(isSqlPreview("Propose a change to a business definition for steward review")).toBe(false);
    expect(isSqlPreview("semantic_query: Berapa omzet per region?")).toBe(false);
    expect(isSqlPreview("Look up approved business definitions")).toBe(false);
  });

  it("shows a tool sentence as text without a SQL label", async () => {
    const { container } = await render(
      <ToolPreview preview="Propose a change to a business definition for steward review" />,
    );
    const root = container as HTMLElement;
    expect(root.querySelector("pre")).toBeNull();
    expect(root.textContent).toBe("Propose a change to a business definition for steward review");
    expect(root.querySelector('[aria-label="Copy SQL"]')).toBeNull();
  });
});

const plan: RailStep = { id: "plan", kind: "thinking", label: "plan", text: "Choosing a skill", status: "done" };
const query: RailStep = { id: "q", kind: "tool", label: "query_execute", text: "Running a SQL query", status: "running" };

describe("process rail", () => {
  it("opens while the run works and folds into its summary once it finishes", async () => {
    const view = await render(<ProcessRail steps={[plan, query]} running />);
    const toggle = page.getByTestId("process-rail-toggle");
    await expect.element(toggle).toHaveAttribute("data-state", "open");
    await expect.element(toggle).toHaveTextContent(/Working/);
    await expect.element(page.getByText("Running a SQL query")).toBeVisible();
    expect(document.querySelectorAll(".animate-spin")).toHaveLength(1);

    await view.rerender(<ProcessRail steps={[plan, { ...query, status: "done" }]} running={false} />);
    await expect.element(toggle).toHaveAttribute("data-state", "closed");
    await expect.element(toggle).toHaveTextContent(/Planned the run, Ran query_execute/);
    expect(document.querySelectorAll(".animate-spin")).toHaveLength(0);
  });

  it("keeps the reader's choice when they close a live run", async () => {
    const view = await render(<ProcessRail steps={[plan, query]} running />);
    const toggle = page.getByTestId("process-rail-toggle");
    await toggle.click();
    await expect.element(toggle).toHaveAttribute("data-state", "closed");
    // Closed while live, the header carries the current step instead.
    await expect.element(toggle).toHaveTextContent("Running a SQL query");
    await view.rerender(<ProcessRail steps={[plan, query, { ...plan, id: "p2", text: "Reading the result" }]} running />);
    await expect.element(toggle).toHaveAttribute("data-state", "closed");
  });

  it("does not invent durations for a replayed run", async () => {
    await render(<ProcessRail steps={[plan, { ...query, status: "done" }]} running={false} />);
    await page.getByTestId("process-rail-toggle").click();
    await expect.element(page.getByText("Choosing a skill")).toBeVisible();
    expect(document.body.textContent).not.toMatch(/\d+\.\ds/);
  });

  it("shows the run time the journal recorded for a replayed run", async () => {
    const steps: RailStep[] = [
      { ...plan, id: "first", status: "done", at: "2026-09-25T00:00:01" },
      { ...query, id: "last", status: "done", at: "2026-09-25T00:00:05" },
    ];
    await render(<ProcessRail steps={steps} running={false} />);
    await expect.element(page.getByTestId("process-rail-toggle")).toHaveTextContent("4.0s");
  });
});
