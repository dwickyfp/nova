import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { render, type RenderResult } from "vitest-browser-react";
import { page, userEvent } from "vitest/browser";
import "@/styles/index.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SidebarProvider } from "@/components/ui/sidebar";
import type { GraphDetailResponse, GraphRunResponse } from "./api";
import TaskDetail from "./task-detail";

const navigate = vi.fn();

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-router")>();
  return { ...actual, useNavigate: () => navigate };
});

const detail: GraphDetailResponse = {
  graph_id: "db1.default.g1",
  node_count: 2,
  nodes: [
    {
      name: "extract",
      task_id: "id_extract",
      schedule_kind: "cron",
      schedule_expr: "0 2 * * *",
      timezone: "UTC",
      overlap_policy: "skip",
      when_expr: null,
      created_by: "alice",
      is_finalizer: false,
      last_state: "success",
    },
    {
      name: "load",
      task_id: "id_load",
      schedule_kind: "manual",
      schedule_expr: null,
      timezone: "UTC",
      overlap_policy: "skip",
      when_expr: null,
      created_by: "alice",
      is_finalizer: false,
      last_state: null,
    },
  ],
  edges: [{ parent_task: "extract", child_task: "load", edge_kind: "after" }],
};

function run(index: number): GraphRunResponse {
  return {
    id: `run${index}`,
    graph_id: "db1.default.g1",
    trigger_type: "schedule",
    state: index % 2 === 0 ? "success" : "failed",
    overlap_policy: "skip",
    started_at: `2026-09-18T0${index}:00:00Z`,
    execution_user: "dwicky.f.putra",
    execution_role: "ACCOUNTADMIN",
    heartbeat_at: null,
    finished_at: `2026-09-18T0${index}:01:00Z`,
  };
}

const requestedUrls: string[] = [];
let totalRuns = 12;

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    requestedUrls.push(url);
    if (url.includes("/nodes/") && url.endsWith("/sql")) {
      const name = url.includes("id_extract") ? "extract" : "load";
      return new Response(
        JSON.stringify({
          task_id: `id_${name}`,
          name,
          sql: `INSERT INTO ${name}_target SELECT * FROM source_table`,
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      );
    }
    if (url.includes("/task-orchestration/runs/")) {
      return new Response(
        JSON.stringify({
          run: run(1),
          node_runs: [
            {
              id: "node-failed",
              task_id: "load",
              state: "failed",
              attempt: 1,
              error_message: "INSERT failed: target table does not exist",
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.includes("/runs?")) {
      const params = new URL(url, "http://localhost").searchParams;
      const limit = Number(params.get("limit"));
      const offset = Number(params.get("offset"));
      const runs = Array.from({ length: Math.max(0, totalRuns - offset) })
        .slice(0, limit)
        .map((_, i) => run(offset + i));
      return new Response(JSON.stringify({ runs, count: totalRuns }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.includes("/task-orchestration/graphs/")) {
      return new Response(JSON.stringify(detail), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify({}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
}

beforeAll(() => {
  mockApi();
});

afterAll(() => {
  vi.restoreAllMocks();
});

async function renderDetail(
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
): Promise<RenderResult> {
  return render(
    <QueryClientProvider client={client}>
      {/* The page owns its own header, which needs the sidebar context. */}
      <SidebarProvider className="h-dvh min-h-0 overflow-hidden">
        <div className="min-w-0 flex-1">
          <TaskDetail graphId="db1.default.g1" />
        </div>
      </SidebarProvider>
    </QueryClientProvider>,
  );
}

describe("TaskDetail", () => {
  it("shows the persisted execution identity when a run is expanded", async () => {
    totalRuns = 2;
    const screen = await renderDetail();
    await userEvent.click(screen.getByRole("row").filter({ hasText: "success" }));
    await expect
      .element(screen.getByText("Executed as dwicky.f.putra · ACCOUNTADMIN"))
      .toBeVisible();
  });

  it("keeps the SQL modal open when a node run state refreshes", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    totalRuns = 0;
    const screen = await renderDetail(client);
    await userEvent.click(screen.getByText("extract", { exact: true }));
    await userEvent.click(
      screen.getByRole("button", { name: "View SQL for extract" }),
    );
    const sql = page
      .getByRole("dialog")
      .getByText("INSERT INTO extract_target SELECT * FROM source_table");
    await expect.element(sql).toBeVisible();
    client.setQueryData(["task-orchestration", "graph", detail.graph_id], {
      ...detail,
      nodes: detail.nodes.map((node) => ({ ...node, last_state: "running" })),
    });
    await expect
      .element(screen.getByText("running", { exact: true }).first())
      .toBeVisible();
    await expect.element(sql).toBeVisible();
    await userEvent.keyboard("{Escape}");
  });

  it.each([1280, 320])(
    "shows the selected node SQL in a modal at %ipx",
    async (width) => {
      await page.viewport(width, 800);
      totalRuns = 0;
      const screen = await renderDetail();
      await expect
        .element(screen.getByRole("button", { name: /View SQL/ }))
        .not.toBeInTheDocument();
      await userEvent.click(screen.getByText("extract", { exact: true }));
      const sqlButton = screen.getByRole("button", {
        name: "View SQL for extract",
      });
      await expect.element(sqlButton).toBeVisible();
      const pane = screen.container
        .querySelector(".react-flow")!
        .getBoundingClientRect();
      expect(sqlButton.element().getBoundingClientRect().right).toBeGreaterThan(
        pane.left + pane.width / 2,
      );
      await userEvent.click(sqlButton);
      await expect
        .element(
          page
            .getByRole("dialog", { name: "Task SQL" })
            .getByText("INSERT INTO extract_target SELECT * FROM source_table"),
        )
        .toBeVisible();
      await userEvent.keyboard("{Escape}");
      await userEvent.click(screen.getByText("load", { exact: true }));
      await userEvent.click(
        screen.getByRole("button", { name: "View SQL for load" }),
      );
      await expect
        .element(
          page
            .getByRole("dialog")
            .getByText("INSERT INTO load_target SELECT * FROM source_table"),
        )
        .toBeVisible();
      await vi.waitFor(() => {
        expect(
          getComputedStyle(page.getByRole("dialog").element()).opacity,
        ).toBe("1");
      });
      await page.screenshot();
      await userEvent.keyboard("{Escape}");
      await page.viewport(1280, 800);
    },
  );

  it("opens an error directly from a failed run in history", async () => {
    totalRuns = 2;
    const screen = await renderDetail();
    await userEvent.click(screen.getByRole("button", { name: "View error" }));
    await expect
      .element(
        page
          .getByRole("dialog", { name: "Run error" })
          .getByText("INSERT failed: target table does not exist"),
      )
      .toBeVisible();
    await userEvent.keyboard("{Escape}");
    await expect.element(page.getByRole("dialog")).not.toBeInTheDocument();
  });

  it.each([
    [1280, "light", 0],
    [1280, "dark", 12],
    [320, "light", 12],
    [320, "dark", 0],
  ] as const)(
    "centres the graph above history at %ipx in %s with %i runs",
    async (width, theme, count) => {
      await page.viewport(width, 800);
      document.documentElement.classList.toggle("dark", theme === "dark");
      totalRuns = count;
      const screen = await renderDetail();
      await expect.element(screen.getByText("extract")).toBeInTheDocument();
      const checkCentre = async () => {
        await vi.waitFor(() => {
          const pane = screen.container
            .querySelector(".react-flow")!
            .getBoundingClientRect();
          const drawer = screen
            .getByText("Run history")
            .element()
            .parentElement!.parentElement!.getBoundingClientRect();
          const nodes = [
            ...screen.container.querySelectorAll(".react-flow__node"),
          ].map((node) => node.getBoundingClientRect());
          const top = Math.min(...nodes.map((node) => node.top));
          const bottom = Math.max(...nodes.map((node) => node.bottom));
          expect(
            Math.abs((top + bottom) / 2 - (pane.top + drawer.top) / 2),
          ).toBeLessThan(3);
          expect(top).toBeGreaterThan(pane.top);
          expect(bottom).toBeLessThan(drawer.top);
          expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
            width,
          );
          expect(document.documentElement.scrollHeight).toBeLessThanOrEqual(
            800,
          );
        });
      };
      await checkCentre();
      await userEvent.click(
        screen.getByRole("button", { name: "Collapse run history" }),
      );
      await checkCentre();
      await userEvent.click(
        screen.getByRole("button", { name: "Expand run history" }),
      );
      await checkCentre();
      await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
      await userEvent.click(screen.getByRole("button", { name: "Fit view" }));
      await checkCentre();
      await page.screenshot();
      document.documentElement.classList.remove("dark");
      await page.viewport(1280, 800);
    },
  );

  it("renders the flow full-bleed with the run history as a bottom drawer", async () => {
    requestedUrls.length = 0;
    totalRuns = 12;
    const screen = await renderDetail();

    // The flow renders both nodes.
    await expect.element(screen.getByText("extract")).toBeInTheDocument();
    await expect.element(screen.getByText("load")).toBeInTheDocument();
    // The drawer's resize handle and heading.
    await expect
      .element(screen.getByRole("separator", { name: "Resize run history" }))
      .toBeInTheDocument();
    await expect.element(screen.getByText("Run history")).toBeInTheDocument();
  });

  it("collapses and expands the run history drawer", async () => {
    requestedUrls.length = 0;
    totalRuns = 12;
    const screen = await renderDetail();

    await userEvent.click(
      screen.getByRole("button", { name: "Collapse run history" }),
    );

    // Collapsed: the handle is gone and the control flips to expand.
    await expect
      .element(screen.getByRole("button", { name: "Expand run history" }))
      .toBeInTheDocument();
    await expect
      .element(screen.getByRole("separator", { name: "Resize run history" }))
      .not.toBeInTheDocument();
  });

  it("requests the first page of runs with a limit and a zero offset", async () => {
    requestedUrls.length = 0;
    totalRuns = 12;
    const screen = await renderDetail();

    await expect.element(screen.getByText("run9")).not.toBeInTheDocument();
    expect(
      requestedUrls.some(
        (url) => url.includes("limit=10") && url.includes("offset=0"),
      ),
    ).toBe(true);
  });

  it("advances the offset when the next page is chosen", async () => {
    requestedUrls.length = 0;
    totalRuns = 12;
    const screen = await renderDetail();

    await vi.waitFor(() =>
      expect(requestedUrls.some((url) => url.includes("offset=0"))).toBe(true),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Go to next page" }),
    );

    await vi.waitFor(() =>
      expect(requestedUrls.some((url) => url.includes("offset=10"))).toBe(true),
    );
  });

  it("shows an empty state when the task has no runs", async () => {
    requestedUrls.length = 0;
    totalRuns = 0;
    const screen = await renderDetail();

    await expect
      .element(screen.getByText("No runs recorded yet"))
      .toBeInTheDocument();
  });
});
