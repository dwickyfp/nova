import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import { api } from "@/lib/api-client";
import {
  TableItemWithPopover,
  TablePopoverProvider,
} from "./table-item-with-popover";
import type { QueryResponse } from "./types";
import "@/styles/index.css";

vi.mock("@/lib/api-client", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const name = "IDX_0572b4a334c84f4ba6fc1b6a785aad1f_V1";
const columns = Array.from({ length: 20 }, (_, i) => ({
  name: `column_${i}`,
  type: "varchar(512)",
}));
const clients: QueryClient[] = [];

function result(overrides: Partial<QueryResponse> = {}): QueryResponse {
  return {
    success: true,
    columns: columns.map((c) => c.name),
    rows: Array.from({ length: 10 }, (_, r) =>
      columns.map((_, c) => `value_${r}_${c}`),
    ),
    row_count: 10,
    affected_rows: 0,
    elapsed_ms: 1,
    original_sql: "",
    executed_sql: "",
    warnings: [],
    ...overrides,
  };
}

async function mount(table: string | string[] = name, database = "analytics") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <TablePopoverProvider>
        <div style={{ width: 256 }}>
          {(Array.isArray(table) ? table : [table]).map((tableName) => (
            <TableItemWithPopover
              key={tableName}
              name={tableName}
              database={database}
              schema="default"
            />
          ))}
        </div>
      </TablePopoverProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.get)
    .mockReset()
    .mockResolvedValue({ columns, count: columns.length });
  vi.mocked(api.post).mockReset().mockResolvedValue([result()]);
});
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  document.documentElement.classList.remove("dark");
});

describe("workspace table hover card", () => {
  it("replaces the active card immediately and keeps one height across tables and loading states", async () => {
    await page.viewport(1280, 800);
    let resolveColumns!: (value: {
      columns: typeof columns;
      count: number;
    }) => void;
    vi.mocked(api.get)
      .mockResolvedValueOnce({ columns: columns.slice(0, 4), count: 4 })
      .mockImplementationOnce(
        () =>
          new Promise<{ columns: typeof columns; count: number }>((resolve) => {
            resolveColumns = resolve;
          }) as ReturnType<typeof api.get>,
      );
    await mount(["categories", name, "empty_table"]);
    let maximumOpen = 0;
    const observer = new MutationObserver(() => {
      maximumOpen = Math.max(
        maximumOpen,
        document.querySelectorAll('[role="dialog"]').length,
      );
    });
    observer.observe(document.body, { childList: true, subtree: true });
    try {
      await userEvent.hover(
        page.getByRole("button", { name: "categories", exact: true }),
      );
      await expect
        .element(page.getByText("4 columns", { exact: true }))
        .toBeVisible();
      const height = page
        .getByRole("dialog")
        .element()
        .getBoundingClientRect().height;
      expect(height).toBe(304);

      await userEvent.hover(page.getByRole("button", { name, exact: true }));
      expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
      expect(
        page.getByRole("dialog").element().getAttribute("aria-label"),
      ).toBe(`${name} table details`);
      await expect.element(page.getByText("Loading columns…")).toBeVisible();
      expect(
        page.getByRole("dialog").element().getBoundingClientRect().height,
      ).toBe(height);
      resolveColumns({ columns, count: 20 });
      await expect
        .element(page.getByText("20 columns", { exact: true }))
        .toBeVisible();
      expect(
        page.getByRole("dialog").element().getBoundingClientRect().height,
      ).toBe(height);

      vi.mocked(api.get).mockResolvedValueOnce({ columns: [], count: 0 });
      await userEvent.hover(
        page.getByRole("button", { name: "empty_table", exact: true }),
      );
      expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
      expect(
        page.getByRole("dialog").element().getAttribute("aria-label"),
      ).toBe("empty_table table details");
      await expect
        .element(page.getByText("No columns available."))
        .toBeVisible();
      expect(
        page.getByRole("dialog").element().getBoundingClientRect().height,
      ).toBe(height);
      await userEvent.hover(
        page.getByRole("tab", { name: "Preview Data", exact: true }),
      );
      await userEvent.click(
        page.getByRole("tab", { name: "Preview Data", exact: true }),
      );
      await expect.element(page.getByRole("table")).toBeVisible();
      await new Promise((resolve) => setTimeout(resolve, 250));
      expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
      expect(
        page.getByRole("dialog").element().getBoundingClientRect().height,
      ).toBe(height);
      expect(maximumOpen).toBe(1);
    } finally {
      observer.disconnect();
    }
  });

  it.each([320, 1280])(
    "keeps both scroll axes inside the same card size at %i px, loading preview only on demand",
    async (width) => {
      await page.viewport(width, 800);
      document.documentElement.classList.toggle("dark", width === 1280);
      await mount();
      const trigger = page.getByRole("button", { name, exact: true });
      await userEvent.hover(trigger);
      await expect
        .element(page.getByText("20 columns", { exact: true }))
        .toBeVisible();
      expect(api.post).not.toHaveBeenCalled();
      const card = page.getByRole("dialog").element();
      const original = card.getBoundingClientRect();
      expect(original.width).toBe(256);
      expect(original.left).toBeGreaterThanOrEqual(0);
      expect(original.right).toBeLessThanOrEqual(width);
      if (width === 1280)
        expect(
          original.left - trigger.element().getBoundingClientRect().right,
        ).toBe(8);
      const details = page
        .getByRole("tabpanel", { name: "Detail", exact: true })
        .element();
      expect(details.scrollHeight).toBeGreaterThan(details.clientHeight);

      await userEvent.unhover(trigger);
      await userEvent.hover(
        page.getByRole("tab", { name: "Preview Data", exact: true }),
      );
      await userEvent.click(
        page.getByRole("tab", { name: "Preview Data", exact: true }),
      );
      await expect.element(page.getByRole("table")).toBeVisible();
      expect(api.post).toHaveBeenCalledWith(
        "/query/execute",
        {
          sql: `SELECT * FROM \`analytics\`.\`${name}\` LIMIT 10`,
          database: "analytics",
          schema: "default",
          max_rows: 10,
        },
        expect.any(AbortSignal),
      );
      const preview = page
        .getByRole("tabpanel", { name: "Preview Data", exact: true })
        .element();
      const next = card.getBoundingClientRect();
      expect(next.width).toBe(original.width);
      expect(next.height).toBe(original.height);
      expect(preview.scrollWidth).toBeGreaterThan(preview.clientWidth);
      expect(preview.scrollHeight).toBeGreaterThan(preview.clientHeight);
      preview.scrollLeft = 100;
      preview.scrollTop = 50;
      expect(preview.scrollLeft).toBe(100);
      expect(preview.scrollTop).toBe(50);
      expect(card.querySelectorAll("tbody tr")).toHaveLength(10);
      await userEvent.click(
        page.getByRole("tab", { name: "Detail", exact: true }),
      );
      await expect
        .element(page.getByText("column_0", { exact: true }))
        .toBeVisible();
      await userEvent.keyboard("{Escape}");
      await expect.element(page.getByRole("dialog")).not.toBeInTheDocument();
    },
  );

  it("quotes identifiers, handles null values and caps the rendered preview at 10 rows", async () => {
    const table = "odd`table";
    vi.mocked(api.post).mockResolvedValue([
      result({
        columns: ["value"],
        rows: Array.from({ length: 12 }, (_, i) => [i === 0 ? null : i]),
      }),
    ]);
    await mount(table, "odd`database");
    await userEvent.click(
      page.getByRole("button", { name: table, exact: true }),
    );
    await userEvent.click(
      page.getByRole("tab", { name: "Preview Data", exact: true }),
    );
    await expect.element(page.getByRole("table")).toBeVisible();
    expect(api.post).toHaveBeenCalledWith(
      "/query/execute",
      expect.objectContaining({
        sql: "SELECT * FROM `odd``database`.`odd``table` LIMIT 10",
        max_rows: 10,
      }),
      expect.any(AbortSignal),
    );
    expect(
      page.getByRole("table").element().querySelectorAll("tbody tr"),
    ).toHaveLength(10);
    await expect.element(page.getByText("NULL", { exact: true })).toBeVisible();
  });

  it("shows loading, engine failure and an empty result after retry without resizing", async () => {
    let resolve!: (value: QueryResponse[]) => void;
    vi.mocked(api.post).mockImplementationOnce(
      () =>
        new Promise<QueryResponse[]>((r) => {
          resolve = r;
        }) as ReturnType<typeof api.post>,
    );
    await mount();
    await userEvent.click(page.getByRole("button", { name, exact: true }));
    await expect
      .element(page.getByText("20 columns", { exact: true }))
      .toBeVisible();
    const card = page.getByRole("dialog").element();
    const height = card.getBoundingClientRect().height;
    await userEvent.click(
      page.getByRole("tab", { name: "Preview Data", exact: true }),
    );
    await expect.element(page.getByText("Loading preview…")).toBeVisible();
    expect(card.getBoundingClientRect().height).toBe(height);
    resolve([result({ success: false, error: "access denied", rows: [] })]);
    await expect
      .element(page.getByRole("alert"))
      .toHaveTextContent("Could not load preview data.");
    vi.mocked(api.post).mockResolvedValueOnce([
      result({ rows: [], row_count: 0 }),
    ]);
    await userEvent.click(
      page.getByRole("button", { name: "Retry", exact: true }),
    );
    await expect.element(page.getByText("No rows to preview.")).toBeVisible();
    expect(card.getBoundingClientRect().height).toBe(height);
  });

  it("opens by keyboard and switches tabs with arrow keys", async () => {
    await mount();
    page.getByRole("button", { name, exact: true }).element().focus();
    await userEvent.keyboard("{Enter}");
    await expect
      .element(page.getByRole("tab", { name: "Detail", exact: true }))
      .toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    await expect.element(page.getByRole("table")).toBeVisible();
    await userEvent.keyboard("{Escape}");
    await expect
      .element(page.getByRole("button", { name, exact: true }))
      .toHaveFocus();
  });
});
