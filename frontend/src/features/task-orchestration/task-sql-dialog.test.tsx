import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/index.css";
import type * as api from "./api";
import { formatTaskSql } from "./presentation";
import { TaskSQLDialog } from "./task-sql-dialog";

const SQL =
  "INSERT INTO XSOURCE_DEMO.order_facts SELECT sf.ORDER_ID, sf.REGION, rt.manager, pg.city, " +
  "sf.AMOUNT AS sf_amount, pg.amount AS pg_amount FROM sf_pat.SALES.ORDERS sf JOIN " +
  "pg_shop.public.orders pg ON pg.id = sf.ORDER_ID JOIN sr_remote.XSOURCE_DEMO.region_targets rt " +
  "ON rt.region = sf.REGION";

const node = {
  name: "xsource_pat_refresh",
  task_id: "t1",
  schedule_kind: "cron",
  schedule_expr: "0 3 * * *",
  timezone: null,
  overlap_policy: "skip",
  when_expr: null,
  created_by: null,
  is_finalizer: false,
  last_state: null,
} as unknown as api.GraphNode;

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
});

describe("Task SQL", () => {
  it("lays the statement out one clause per line", () => {
    const lines = formatTaskSql(SQL).split("\n").map((line) => line.trim());
    expect(lines[0]).toBe("INSERT INTO");
    expect(lines).toContain("FROM");
    expect(lines.filter((line) => line.startsWith("JOIN"))).toHaveLength(2);
  });

  it("keeps a statement the formatter cannot read as stored", () => {
    const odd = "COPY INTO t FROM @stage/path ((";
    expect(formatTaskSql(odd)).toBe(odd);
  });

  it("shows the task SQL formatted and highlighted", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ sql: SQL }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const screen = await render(
      <QueryClientProvider client={client}>
        <TaskSQLDialog graphId="g1" node={node} />
      </QueryClientProvider>,
    );
    await screen.getByRole("button", { name: "View SQL for xsource_pat_refresh" }).click();
    const code = screen.getByLabelText("Task SQL syntax highlighted code");
    await expect.element(code).toBeVisible();
    expect(code.element().querySelectorAll(".hljs-keyword").length).toBeGreaterThan(5);
    expect(code.element().textContent).toContain("\nFROM\n");
  });

  it("shows a Python task as Python, unformatted, with its packages", async () => {
    const code = "import novaduck\ncon = novaduck.connect()\nprint(con.sql('SELECT 1'))";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          task_id: "t2",
          name: "score",
          sql: code,
          language: "python",
          packages: ["scikit-learn==1.5.2", "tabulate"],
          runtime_version: "3.12",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const screen = await render(
      <QueryClientProvider client={client}>
        <TaskSQLDialog graphId="g1" node={{ ...node, name: "score", language: "python" }} />
      </QueryClientProvider>,
    );
    await screen.getByRole("button", { name: "View Python for score" }).click();
    await expect.element(screen.getByRole("heading", { name: "Task Python" })).toBeVisible();
    await expect.element(screen.getByText("score · Python 3.12")).toBeVisible();
    await expect
      .element(screen.getByText("Packages: scikit-learn==1.5.2, tabulate"))
      .toBeVisible();
    expect(screen.getByLabelText("Task Python code").element().textContent).toBe(code);
  });
});
