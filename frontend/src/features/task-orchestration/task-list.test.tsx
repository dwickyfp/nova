import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { render, type RenderResult } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { GraphSummary } from "./api";
import TaskList from "./task-list";

const navigate = vi.fn();

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-router")>();
  return { ...actual, useNavigate: () => navigate };
});

function graph(overrides: Partial<GraphSummary> = {}): GraphSummary {
  return {
    graph_id: "db1.default.g1",
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

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
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
      <TaskList />
    </QueryClientProvider>,
  );
}

describe("TaskList", () => {
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

    await expect.element(screen.getByText("never run")).toBeInTheDocument();
  });

  it("shows an empty state when there are no tasks", async () => {
    graphs = [];
    const screen = await renderList();

    await expect.element(screen.getByText("No tasks yet")).toBeInTheDocument();
  });
});
