import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { DndContext } from "@dnd-kit/core";
import { SortableContext } from "@dnd-kit/sortable";
import { NotebookCellView } from "./cell";
import { cellForFile, formatCell, formatDuration } from "./helpers";
import { isEditableName, isNotebookName, nextNotebookName } from "./names";
import { CellOutputs } from "./outputs";
import { parseSseBlock } from "./stream";
import type { CellOutput, NotebookCell } from "./types";
import { isStale, type CellRun } from "./use-notebook";

const cell = (overrides: Partial<NotebookCell> = {}): NotebookCell => ({
  id: "c1",
  kind: "sql",
  source: "SELECT 1",
  name: "result_1",
  engine: "duckdb",
  source_revision: "sha256:a",
  ...overrides,
});

const run = (overrides: Partial<CellRun> = {}): CellRun => ({
  executionId: "e1",
  status: "succeeded",
  executionCount: 3,
  outputs: [],
  durationMs: 120,
  message: null,
  sourceRevision: "sha256:a",
  generation: 1,
  startedAt: null,
  runtimeId: "rt-1",
  ...overrides,
});

describe("notebook names and files", () => {
  it("recognises notebooks and what Workspaces opens as text", () => {
    expect(isNotebookName("Revenue.IPYNB")).toBe(true);
    expect(isNotebookName("query.sql")).toBe(false);
    expect(isEditableName("query.sql")).toBe(true);
    expect(isEditableName("Untitled")).toBe(true);
    expect(isEditableName("sales.parquet")).toBe(false);
    expect(nextNotebookName(["Untitled notebook.ipynb", "untitled notebook 2.ipynb"])).toBe(
      "Untitled notebook 3.ipynb",
    );
  });

  it("starts a dropped data file with a bounded preview query, quoted safely", () => {
    expect(cellForFile("data/sales.csv")).toEqual({
      kind: "sql",
      source: "SELECT *\nFROM read_csv('data/sales.csv')\nLIMIT 100",
    });
    expect(cellForFile("data/o'brien.parquet")?.source).toContain("read_parquet('data/o''brien.parquet')");
    expect(cellForFile("data/book.xlsx")?.kind).toBe("python");
    expect(cellForFile("src/model.py")).toBeNull();
  });

  it("formats values and durations for a reader", () => {
    expect(formatCell(null)).toBe("null");
    expect(formatCell(1482356955)).toBe("1,482,356,955");
    expect(formatDuration(711)).toBe("711 ms");
    expect(formatDuration(9_900)).toBe("9.9 s");
  });
});

describe("stale outputs", () => {
  it("marks output stale after an edit, a kernel restart, a new runtime or a move", () => {
    expect(isStale(cell(), run(), 1, new Set())).toBe(false);
    expect(isStale(cell({ source_revision: "sha256:b" }), run(), 1, new Set())).toBe(true);
    expect(isStale(cell(), run(), 2, new Set())).toBe(true);
    expect(isStale(cell(), run(), 1, new Set(), "rt-2")).toBe(true);
    expect(isStale(cell(), run(), 1, new Set(["c1"]))).toBe(true);
    expect(isStale(cell(), run({ status: "running" }), 2, new Set())).toBe(false);
  });
});

describe("event stream frames", () => {
  it("keeps the event id used to resume", () => {
    expect(parseSseBlock('id: 17-0\nevent: cell.finished\ndata: {"a":1}')).toEqual({
      id: "17-0",
      event: "cell.finished",
      data: { a: 1 },
    });
    expect(parseSseBlock(": keep-alive")).toBeNull();
  });
});

describe("cell outputs", () => {
  it("shows HTML only in a sandboxed frame without scripts or same origin", async () => {
    const outputs: CellOutput[] = [
      { type: "html", html: "<script>parent.alert(1)</script><b>hi</b>", text: "hi" },
    ];
    const screen = await render(<CellOutputs outputs={outputs} />);
    const frame = screen.container.querySelector("iframe");
    expect(frame).not.toBeNull();
    expect(frame?.getAttribute("sandbox")).toBe("");
    expect(frame?.getAttribute("srcdoc")).toContain("default-src 'none'");
    expect(screen.container.querySelector("script")).toBeNull();
  });

  it("labels a preview honestly and where it was computed", async () => {
    const outputs: CellOutput[] = [
      {
        type: "table",
        name: "sales",
        table: {
          schema: [{ name: "region", type: "VARCHAR" }],
          rows: [["Jakarta"], ["Bandung"]],
          total_rows: null,
          truncated: true,
          origin: "nova",
        },
      },
    ];
    const screen = await render(<CellOutputs outputs={outputs} />);
    await expect.element(screen.getByText("First 2 rows, total not counted")).toBeVisible();
    await expect.element(screen.getByText("Computed by Nova")).toBeVisible();
  });

  it("shows an error with its traceback folded", async () => {
    const outputs: CellOutput[] = [
      { type: "error", ename: "ZeroDivisionError", evalue: "division by zero", traceback: ["1/0"] },
    ];
    const screen = await render(<CellOutputs outputs={outputs} />);
    await expect.element(screen.getByText("ZeroDivisionError: division by zero")).toBeVisible();
    await screen.getByRole("button", { name: "Traceback" }).click();
    await expect.element(screen.getByText("1/0")).toBeVisible();
  });
});

describe("cell", () => {
  async function renderCell(props: Partial<Parameters<typeof NotebookCellView>[0]> = {}) {
    const onCommand = vi.fn();
    const screen = await render(
      <DndContext>
        <SortableContext items={["c1"]}>
          <NotebookCellView
            cell={cell()}
            index={1}
            count={3}
            run={run()}
            stale={false}
            active={false}
            editing={false}
            runtimeReady
            canEdit
            onSelect={vi.fn()}
            onSource={vi.fn()}
            onKind={vi.fn()}
            onName={vi.fn()}
            onEngine={vi.fn()}
            onKey={vi.fn()}
            onCommand={onCommand}
            {...props}
          />
        </SortableContext>
      </DndContext>,
    );
    return { screen, onCommand };
  }

  it("moves with the keyboard from its handle", async () => {
    const { screen, onCommand } = await renderCell();
    const handle = screen.getByRole("button", { name: "Move SQL cell 2 of 3" });
    (handle.element() as HTMLElement).focus();
    await handle.element().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
    await handle.element().dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true }));
    expect(onCommand).toHaveBeenNthCalledWith(1, "c1", "move-up");
    expect(onCommand).toHaveBeenNthCalledWith(2, "c1", "move-bottom");
  });

  it("cannot be moved while it runs and offers Stop", async () => {
    const { screen, onCommand } = await renderCell({ run: run({ status: "running", startedAt: Date.now() }) });
    expect(screen.getByRole("button", { name: "Move SQL cell 2 of 3" }).element()).toHaveProperty(
      "disabled",
      true,
    );
    await screen.getByRole("button", { name: "Stop this cell" }).click();
    expect(onCommand).toHaveBeenCalledWith("c1", "stop");
  });

  it("offers Ask Neva on a failed cell and shows the execution count", async () => {
    const { screen, onCommand } = await renderCell({ run: run({ status: "failed" }) });
    await expect.element(screen.getByText("[3]")).toBeVisible();
    await screen.getByRole("button", { name: "Ask Neva" }).click();
    expect(onCommand).toHaveBeenCalledWith("c1", "ask-neva");
  });

  it("switches a SQL cell between DuckDB and Nova", async () => {
    const onEngine = vi.fn();
    const { screen } = await renderCell({ onEngine });
    await screen.getByRole("radio", { name: "Nova" }).click();
    expect(onEngine).toHaveBeenCalledWith("c1", "nova");
  });
});
