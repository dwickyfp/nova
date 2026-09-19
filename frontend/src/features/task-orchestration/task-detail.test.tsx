import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { render, type RenderResult } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
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

async function renderDetail(): Promise<RenderResult> {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {/* The page owns its own header, which needs the sidebar context. */}
      <SidebarProvider>
        <TaskDetail graphId="db1.default.g1" />
      </SidebarProvider>
    </QueryClientProvider>,
  );
}

describe("TaskDetail", () => {
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
