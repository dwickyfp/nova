import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import type { LayoutBlock } from "./block";
import { chartPosition, LayoutView } from "./layout-view";
import "@/styles/index.css";

const BLOCK: LayoutBlock = {
  catalog_version: "nova-layout-1",
  fallback: "Revenue was 1,200 in June and 1,500 in July.",
  state_defaults: {},
  provenance: { composer: "model", evidence_ids: ["evidence_1"] },
};

// What the server sends after checking and rewriting each statement.
const STATEMENTS = [
  "root = Page([header, scenario, kpis, detail, summary])",
  'header = Header("Sales overview", "June and July 2025, with a revenue what-if.")',
  "$uplift = 10",
  'scenario = Controls("What-if scenario", [Slider("Revenue change", $uplift, -30, 50, 5, "percent")], "Order volume stays constant.")',
  "base = 1500",
  'kpis = KpiTiles([Metric("July revenue", base, "number", 0.25, "vs. June"), Metric("Scenario revenue", base * (1 + $uplift / 100), "number", $uplift / 100, "Scenario"), Metric("Orders", 42, "number", null, "Held constant")])',
  'detail = Section("Detail", [ResultTable("Revenue by month", ["month", "revenue"], [["2025-06", 1200], ["2025-07", 1500]])])',
  'summary = Text("Revenue rose **25%** from June to July.")',
];
const PROGRAM = STATEMENTS.join("\n");

function show(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <div style={{ width: 720 }}>{node}</div>
    </QueryClientProvider>,
  );
}

function view(program: string, streaming = false, block: LayoutBlock = BLOCK) {
  return (
    <LayoutView block={block} program={program} streaming={streaming} ready={false} />
  );
}

function tile(container: HTMLElement, label: string): HTMLElement {
  const figure = [...container.querySelectorAll<HTMLElement>("[data-figure]")].find(
    (item) => item.querySelector("dt")?.textContent === label,
  );
  if (!figure) throw new Error(`No tile ${label}`);
  return figure;
}

afterEach(() => {
  cleanup();
});

describe("LayoutView", () => {
  it("draws a page: title, subtitle, controls, KPI tiles with change and badge, rows and narrative", async () => {
    const screen = await show(view(PROGRAM));
    await expect.element(screen.getByRole("heading", { name: "Sales overview", level: 2 })).toBeVisible();
    await expect.element(screen.getByText("June and July 2025, with a revenue what-if.")).toBeVisible();
    await expect.element(screen.getByRole("heading", { name: "What-if scenario" })).toBeVisible();
    await expect.element(screen.getByRole("slider", { name: "Revenue change" })).toHaveValue("10");
    const july = tile(screen.container, "July revenue");
    expect(july.textContent).toMatch(/1[.,\s]?500/);
    expect(july.textContent).toContain("+25%");
    expect(july.textContent).toContain("vs. June");
    expect(tile(screen.container, "Orders").textContent).toContain("Held constant");
    await expect.element(screen.getByRole("heading", { name: "Detail" })).toBeVisible();
    await expect.element(screen.getByText("Revenue by month")).toBeVisible();
    await expect.element(screen.getByText("Revenue rose")).toBeVisible();
    expect(screen.container.querySelector('[aria-label="Composing the page"]')).toBeNull();
  });

  it("recomputes scenario figures in the browser when a control moves, without a request", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const screen = await show(view(PROGRAM));
    const scenario = () => tile(screen.container, "Scenario revenue").textContent ?? "";
    await vi.waitFor(() => expect(scenario()).toMatch(/1[.,\s]?650/));
    expect(scenario()).toContain("+10%");
    const slider = screen.getByRole("slider", { name: "Revenue change" });
    await userEvent.click(slider);
    (slider.element() as HTMLInputElement).focus();
    await userEvent.keyboard("{Home}");
    await vi.waitFor(() => expect(scenario()).toMatch(/1[.,\s]?050/));
    expect(scenario()).toContain("-30%");
    // The baseline is a verified figure and does not move.
    expect(tile(screen.container, "July revenue").textContent).toMatch(/1[.,\s]?500/);
    expect(fetch).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("shows each statement as it streams in, in reading order", async () => {
    const screen = await show(view(STATEMENTS.slice(0, 2).join("\n") + "\n", true));
    await expect.element(screen.getByRole("heading", { name: "Sales overview" })).toBeVisible();
    await expect.element(screen.getByRole("status", { name: "Composing the page" })).toBeVisible();
    expect(screen.container.querySelectorAll("[data-figure]")).toHaveLength(0);
    await screen.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <div style={{ width: 720 }}>{view(STATEMENTS.slice(0, 6).join("\n") + "\n", true)}</div>
      </QueryClientProvider>,
    );
    await vi.waitFor(() => expect(screen.container.querySelectorAll("[data-figure]")).toHaveLength(3));
    expect(screen.container.querySelector('[data-component="ResultTable"]')).toBeNull();
  });

  it("replays a stored page exactly as it streamed", async () => {
    const live = await show(view(STATEMENTS[0] + "\n", true));
    for (let end = 2; end <= STATEMENTS.length; end += 1) {
      await live.rerender(
        <QueryClientProvider client={new QueryClient()}>
          <div style={{ width: 720 }}>{view(STATEMENTS.slice(0, end).join("\n") + "\n", true)}</div>
        </QueryClientProvider>,
      );
    }
    await live.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <div style={{ width: 720 }}>{view(PROGRAM + "\n", false)}</div>
      </QueryClientProvider>,
    );
    await expect.element(live.getByText("Revenue rose")).toBeVisible();
    const streamed = live.container.querySelector("article")?.innerHTML;
    // A fresh mount of the stored program, as a reload replays it.
    await live.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <div key="replay" style={{ width: 720 }}>{view(PROGRAM + "\n", false)}</div>
      </QueryClientProvider>,
    );
    await expect.element(live.getByText("Revenue rose")).toBeVisible();
    const stored = live.container.querySelector("article")?.innerHTML;
    // React ids differ between mounts; everything else is the same page.
    const strip = (html = "") => html.replace(/(id|for|aria-labelledby|aria-controls)="[^"]*"/g, "");
    expect(strip(stored)).toBe(strip(streamed));
  });

  it("leaves out a statement it cannot draw and keeps the rest of the page", async () => {
    const program = [
      "root = Page([header, broken, unknown, summary])",
      'header = Header("Sales overview")',
      'broken = KpiTiles([Metric("No value given")])',
      'unknown = Gauge("Speed", 3)',
      'summary = Text("Still here.")',
    ].join("\n");
    const screen = await show(view(program));
    await expect.element(screen.getByRole("heading", { name: "Sales overview" })).toBeVisible();
    await expect.element(screen.getByText("Still here.")).toBeVisible();
    expect(screen.container.textContent).not.toContain("Speed");
  });

  it("shows the written answer when the page has nothing to draw", async () => {
    const screen = await show(view(""));
    await expect.element(screen.getByText("Revenue was 1,200 in June and 1,500 in July.")).toBeVisible();
    await screen.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <div key="broken">{view("root = Gauge(1)")}</div>
      </QueryClientProvider>,
    );
    await expect.element(screen.getByText("Revenue was 1,200 in June and 1,500 in July.")).toBeVisible();
  });

  it("marks a replayed answer from an earlier version as read-only", async () => {
    const screen = await show(
      view('root = Page([t])\nt = Text("Earlier answer")', false, {
        ...BLOCK,
        provenance: { legacy: true },
      }),
    );
    await expect.element(screen.getByText("An earlier interactive answer, shown read-only.")).toBeVisible();
  });
});

describe("chartPosition", () => {
  it("reads a chart's position on its page from the signed reference", () => {
    const payload = btoa(JSON.stringify({ c: "content-3", n: 2, t: "t1" })).replace(/=+$/, "");
    expect(chartPosition(`dr1.${payload}.mac`)).toBe(2);
    expect(chartPosition("dr1.e30.mac")).toBeNull();
    expect(chartPosition("not-a-ref")).toBeNull();
    expect(chartPosition(undefined)).toBeNull();
  });
});
