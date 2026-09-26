import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { render, type RenderResult } from "vitest-browser-react";
import { page, userEvent } from "vitest/browser";
import "@/styles/index.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { GraphSummary, NodeRunResponse } from "./api";
import TaskList from "./task-list";
import { formatTimestamp } from "./presentation";
import { toast } from "sonner";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const navigate = vi.fn();

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-router")>();
  return { ...actual, useNavigate: () => navigate };
});

function graph(overrides: Partial<GraphSummary> = {}): GraphSummary {
  return {
    graph_id: "db1.default.g1",
    owner_role: "analyst",
    can_run: true,
    root_task: "nightly_load",
    database_name: "db1",
    schema_name: "default",
    node_count: 1,
    schedule_kind: "cron",
    schedule_expr: "0 2 * * *",
    timezone: "UTC",
    overlap_policy: "skip",
    last_run: null,
    run_counts: { total: 7, success: 5, failed: 2 },
    created_at: "2026-09-18T03:00:00Z",
    ...overrides,
  };
}

let graphs: GraphSummary[] = [graph()];
let runStatus = 202;
const runRequests: string[] = [];
let errorNodes: Partial<NodeRunResponse>[] = [];
let detailStatus = 200;

function mockApi() {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/task-orchestration/runs/")) {
        return new Response(
          JSON.stringify(
            detailStatus === 200
              ? {
                  run: {
                    id: "failed-run",
                    state: "failed",
                    started_at: "2026-09-24T08:00:00Z",
                  },
                  node_runs: errorNodes,
                }
              : { detail: "Unavailable" },
          ),
          {
            status: detailStatus,
            headers: { "Content-Type": "application/json" },
          },
        );
      }
      if (init?.method === "POST") {
        runRequests.push(url);
        return new Response(
          JSON.stringify(
            runStatus === 202
              ? { id: "manual-run", state: "pending" }
              : { detail: "This task already has an active run" },
          ),
          {
            status: runStatus,
            headers: { "Content-Type": "application/json" },
          },
        );
      }
      if (url.includes("/task-orchestration/graphs")) {
        return new Response(JSON.stringify({ graphs, count: graphs.length }), {
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

async function renderList(): Promise<RenderResult> {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <div className="flex h-dvh min-w-0 flex-col overflow-hidden p-4">
        <TaskList />
      </div>
    </QueryClientProvider>,
  );
}

describe("TaskList", () => {
  it("shows ownership and prevents Run when another role is active", async () => {
    graphs = [graph({ owner_role: "etl_team", can_run: false })];
    runRequests.length = 0;
    const screen = await renderList();
    await expect
      .element(screen.getByText("etl_team", { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByRole("button", { name: "Run nightly_load" }))
      .toBeDisabled();
    expect(runRequests).toEqual([]);
  });

  it.each([1280, 320])(
    "opens failed-run errors without navigating at %ipx",
    async (width) => {
      await page.viewport(width, 800);
      navigate.mockClear();
      detailStatus = 200;
      graphs = [
        graph({
          last_run: {
            id: "failed-run",
            state: "failed",
            trigger_type: "manual",
            overlap_policy: "skip",
            started_at: "2026-09-24T08:00:00Z",
            finished_at: null,
          },
        }),
      ];
      errorNodes = [
        {
          id: "node1",
          task_id: "nightly_load",
          state: "failed",
          attempt: 1,
          error_message: "Table not found: missing_table\n".repeat(60),
        },
      ];
      const screen = await renderList();
      await userEvent.click(screen.getByRole("button", { name: "View error" }));
      const dialog = page.getByRole("dialog", { name: "Run error" });
      await expect.element(dialog.getByText(/Table not found/)).toBeVisible();
      expect(navigate).not.toHaveBeenCalled();
      const bounds = dialog.element().getBoundingClientRect();
      expect(bounds.left).toBeGreaterThanOrEqual(0);
      expect(bounds.right).toBeLessThanOrEqual(width);
      expect(bounds.bottom).toBeLessThanOrEqual(800);
      expect(bounds.top).toBeGreaterThanOrEqual(0);
      await page.screenshot();
      await userEvent.keyboard("{Escape}");
      await expect.element(dialog).not.toBeInTheDocument();
      expect(navigate).not.toHaveBeenCalled();
      await page.viewport(1280, 800);
    },
  );

  it("retries loading an error and explains when no message was recorded", async () => {
    detailStatus = 503;
    errorNodes = [];
    graphs = [
      graph({
        last_run: {
          id: "failed-run",
          state: "failed",
          trigger_type: "manual",
          overlap_policy: "skip",
          started_at: null,
          finished_at: null,
        },
      }),
    ];
    const screen = await renderList();
    await userEvent.click(screen.getByRole("button", { name: "View error" }));
    await expect
      .element(page.getByText("Could not load the error"))
      .toBeVisible();
    detailStatus = 200;
    await userEvent.click(page.getByRole("button", { name: "Retry" }));
    await expect
      .element(
        page.getByText(/This run failed without a recorded error message/),
      )
      .toBeVisible();
    await userEvent.keyboard("{Escape}");
  });

  it.each([
    [1280, "dark", 1],
    [320, "light", 15],
  ] as const)(
    "contains table scrolling at %ipx in %s with %i tasks",
    async (width, theme, count) => {
      await page.viewport(width, 800);
      document.documentElement.classList.toggle("dark", theme === "dark");
      graphs = Array.from({ length: count }, (_, index) =>
        graph({
          graph_id: `db1.default.task${index}`,
          root_task: `task${index}`,
        }),
      );
      const screen = await renderList();
      await expect
        .element(screen.getByText("task0", { exact: true }))
        .toBeInTheDocument();
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
      expect(document.documentElement.scrollHeight).toBeLessThanOrEqual(800);
      await expect
        .element(screen.getByRole("heading", { name: "Tasks" }))
        .toBeVisible();
      await page.screenshot();
      document.documentElement.classList.remove("dark");
      await page.viewport(1280, 800);
    },
  );
  it("shows the latest run start time", async () => {
    graphs = [
      graph({
        last_run: {
          id: "run1",
          state: "success",
          trigger_type: "manual",
          overlap_policy: "skip",
          started_at: "2026-09-24T08:00:00Z",
          finished_at: "2026-09-24T08:01:00Z",
        },
      }),
    ];
    const screen = await renderList();
    await expect
      .element(screen.getByRole("columnheader", { name: "Last run" }))
      .toBeInTheDocument();
    await expect
      .element(
        screen.getByText(formatTimestamp(graphs[0].last_run!.started_at)),
      )
      .toBeInTheDocument();
  });

  it.each(["click", "Enter", "Space"])(
    "runs without opening the detail page via %s",
    async (activation) => {
      graphs = [graph()];
      runStatus = 202;
      runRequests.length = 0;
      navigate.mockClear();
      const screen = await renderList();
      const button = screen.getByRole("button", { name: "Run nightly_load" });
      await expect.element(button).toBeInTheDocument();
      if (activation === "click") await userEvent.click(button);
      else {
        (button.element() as HTMLButtonElement).focus();
        await userEvent.keyboard(activation === "Enter" ? "{Enter}" : " ");
      }
      await vi.waitFor(() =>
        expect(runRequests).toEqual([
          "/api/v1/task-orchestration/graphs/db1.default.g1/runs",
        ]),
      );
      expect(navigate).not.toHaveBeenCalled();
      await vi.waitFor(() =>
        expect(toast.success).toHaveBeenCalledWith(
          "Run queued for nightly_load",
        ),
      );
      await expect
        .element(screen.getByText("Loading tasks..."))
        .not.toBeInTheDocument();
    },
  );

  it("shows a rejected run and allows retry", async () => {
    graphs = [graph()];
    runStatus = 409;
    const screen = await renderList();
    const button = screen.getByRole("button", { name: "Run nightly_load" });
    await userEvent.click(button);
    await vi.waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "This task already has an active run",
      ),
    );
    await expect.element(button).toBeEnabled();
    runStatus = 202;
  });
  it("shows the run tallies for a task", async () => {
    graphs = [graph()];
    const screen = await renderList();

    await expect.element(screen.getByText("nightly_load")).toBeInTheDocument();
    await expect
      .element(screen.getByText("Cron · 0 2 * * *"))
      .toBeInTheDocument();
    await expect.element(screen.getByText("db1.default")).toBeInTheDocument();
    // The three tallies: total 7, success 5, failed 2, read off the cells so a
    // digit inside the schedule expression cannot satisfy the assertion.
    await expect
      .element(screen.getByRole("cell", { name: "7", exact: true }))
      .toBeInTheDocument();
    await expect
      .element(screen.getByRole("cell", { name: "5", exact: true }))
      .toBeInTheDocument();
    await expect
      .element(screen.getByRole("cell", { name: "2", exact: true }))
      .toBeInTheDocument();
  });

  it("navigates to the detail page when a row is clicked", async () => {
    navigate.mockClear();
    graphs = [graph()];
    const screen = await renderList();

    const row = screen.getByText("nightly_load");
    await userEvent.click(row);

    expect(navigate).toHaveBeenCalledWith({
      to: "/tasks/$graphId",
      params: { graphId: "db1.default.g1" },
    });
  });

  it("filters rows by search text", async () => {
    graphs = [
      graph(),
      graph({
        graph_id: "db1.default.other",
        root_task: "other_task",
      }),
    ];
    const screen = await renderList();

    await userEvent.fill(
      screen.getByPlaceholder("Search task, database, schedule..."),
      "other",
    );

    await expect.element(screen.getByText("other_task")).toBeInTheDocument();
    await expect
      .element(screen.getByText("nightly_load"))
      .not.toBeInTheDocument();
  });

  it('shows a "never run" badge when a task has no runs', async () => {
    graphs = [
      graph({
        last_run: null,
        run_counts: { total: 0, success: 0, failed: 0 },
      }),
    ];
    const screen = await renderList();

    await expect
      .element(screen.getByText("never run", { exact: true }))
      .toBeInTheDocument();
  });

  it("shows an empty state when there are no tasks", async () => {
    graphs = [];
    const screen = await renderList();

    await expect.element(screen.getByText("No tasks yet")).toBeInTheDocument();
  });
});
