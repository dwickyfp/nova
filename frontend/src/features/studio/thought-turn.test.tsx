import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { page } from "vitest/browser";
import { ProcessRail, SqlDisclosure, type RailStep } from "./thought-turn";

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

  it("shows a finished specialist as one branch with its result and recorded time", async () => {
    const onOpenChild = vi.fn();
    const steps: RailStep[] = [
      { id: "called", kind: "delegate", label: "queued", text: "Called Finance", childRunId: "fin", actor: "Finance", body: "Quantify revenue by channel", at: "2026-09-25T00:00:01", status: "done" },
      { id: "tool", kind: "tool", label: "semantic_query", text: "Finance called semantic_query", childRunId: "fin", actor: "Finance", status: "done" },
      { id: "end", kind: "delegate", label: "completed", text: "Finance finished", childRunId: "fin", actor: "Finance", detail: "Revenue grew 12% vs last month", at: "2026-09-25T00:00:05", status: "done" },
    ];
    await render(<ProcessRail steps={steps} running={false} onOpenChild={onOpenChild} />);
    await page.getByTestId("process-rail-toggle").click();

    await expect.element(page.getByText("Quantify revenue by channel")).toBeVisible();
    await expect.element(page.getByText("Revenue grew 12% vs last month")).toBeVisible();
    await expect.element(page.getByLabelText("Run steps").getByText("4.0s")).toBeVisible();
    // The specialist's own steps fold behind the result until asked for.
    await expect.element(page.getByText("Finance called semantic_query")).not.toBeInTheDocument();
    await page.getByRole("button", { name: "1 step" }).click();
    await expect.element(page.getByText("Finance called semantic_query")).toBeVisible();

    await page.getByRole("button", { name: "Called Finance. Open subagent conversation" }).click();
    expect(onOpenChild).toHaveBeenCalledWith("fin");
  });
});
