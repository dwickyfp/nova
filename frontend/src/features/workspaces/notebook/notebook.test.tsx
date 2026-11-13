import { describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import { DndContext } from "@dnd-kit/core";
import { SortableContext } from "@dnd-kit/sortable";
import { NotebookCellView } from "./cell";
import { readToken } from "@/lib/read-token";
import { cellForFile, columnKind, formatCell, formatDuration, INTERACTIVE_POLICY, needsScripts } from "./helpers";
import { isEditableName, isNotebookName, nextNotebookName } from "./names";
import { CellOutputs } from "./outputs";
import { parseSseBlock } from "./stream";
import type { CellOutput, NotebookCell } from "./types";
import { isStale, type CellRun } from "./use-notebook";
import "@/styles/index.css";

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
      { type: "html", html: '<b>hi</b><img src="x" onerror="parent.alert(1)">', text: "hi" },
    ];
    const screen = await render(<CellOutputs outputs={outputs} />);
    const frame = screen.container.querySelector("iframe");
    expect(frame).not.toBeNull();
    expect(frame?.getAttribute("sandbox")).toBe("");
    expect(frame?.getAttribute("srcdoc")).toContain("default-src 'none'");
    expect(screen.container.querySelector("script")).toBeNull();
  });

  it("lets interactive output run its scripts only in an opaque, offline frame", async () => {
    const outputs: CellOutput[] = [
      { type: "html", html: "<div id=gw></div><script>window.x = 1</script>", text: "" },
    ];
    const screen = await render(<CellOutputs outputs={outputs} />);
    const frame = screen.container.querySelector("iframe");
    expect(frame?.getAttribute("sandbox")).toBe("allow-scripts allow-downloads");
    expect(frame?.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(frame?.getAttribute("srcdoc")).toContain(INTERACTIVE_POLICY);
    expect(INTERACTIVE_POLICY).toContain("default-src 'none'");
    expect(INTERACTIVE_POLICY).not.toMatch(/https?:/);
    // Storage stand-ins run before the output's own scripts.
    const srcdoc = frame?.getAttribute("srcdoc") ?? "";
    expect(srcdoc.indexOf("localStorage")).toBeGreaterThan(-1);
    expect(srcdoc.indexOf("localStorage")).toBeLessThan(srcdoc.indexOf('<div id=gw>'));
  });

  it("treats a nested frame (PyGWalker) as interactive and plain markup as static", () => {
    expect(needsScripts('<iframe srcdoc="&lt;script&gt;x&lt;/script&gt;"></iframe>')).toBe(true);
    expect(needsScripts("<b>scripted</b> text about <scripts>")).toBe(false);
  });

  it("names column types by kind, whichever engine reported them", () => {
    expect(["BIGINT", "DECIMAL(18,2)", "double", "int64", "Float64", "HUGEINT"].map(columnKind)).toEqual(
      Array(6).fill("number"),
    );
    expect(["VARCHAR", "str", "object", "Utf8", "UUID"].map(columnKind)).toEqual(Array(5).fill("text"));
    expect(columnKind("DATE")).toBe("date");
    expect(columnKind("TIMESTAMP WITH TIME ZONE")).toBe("timestamp");
    expect(columnKind("datetime64[ns]")).toBe("timestamp");
    expect(columnKind("TIME")).toBe("time");
    expect(columnKind("BOOLEAN")).toBe("boolean");
    expect(columnKind("STRUCT(a INTEGER)")).toBe("nested");
    expect(columnKind("INTEGER[]")).toBe("nested");
    expect(columnKind("BLOB")).toBe("binary");
    expect(columnKind("GEOMETRY")).toBe("other");
  });

  it("shows a type icon, keeps the exact type in the tooltip, and lines between columns", async () => {
    const outputs: CellOutput[] = [
      {
        type: "table",
        table: {
          schema: [
            { name: "month", type: "DATE" },
            { name: "revenue", type: "DOUBLE" },
          ],
          rows: [["2026-07-01", 1482356955]],
          total_rows: 1,
          truncated: false,
          origin: "nova",
        },
      },
    ];
    const screen = await render(<CellOutputs outputs={outputs} />);
    const headers = screen.container.querySelectorAll("th");
    expect(headers[0].getAttribute("title")).toBe("DATE");
    expect(headers[1].getAttribute("title")).toBe("DOUBLE");
    expect(headers[0].textContent).not.toContain("date ");
    expect(headers[0].querySelector("svg")).not.toBeNull();
    expect(headers[1].className).toContain("text-right");
    expect(headers[0].className).toContain("border-r");
    expect(screen.container.querySelector("td")?.className).toContain("border-r");
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
  function contrast(text: string, background: string) {
    const luminance = (color: string) => {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 1;
      const context = canvas.getContext("2d")!;
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      const channels = Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3);
      const [r, g, b] = channels.map((value) => {
        const channel = value / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return r * 0.2126 + g * 0.7152 + b * 0.0722;
    };
    const levels = [luminance(text), luminance(background)].sort((a, b) => b - a);
    return (levels[0] + 0.05) / (levels[1] + 0.05);
  }

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
            active={false}
            editing={false}
            canEdit
            canRun
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

  it.each(["sql", "python"] as const)("highlights %s before editing and escapes code markup", async (kind) => {
    const source = kind === "sql" ? "SELECT '<img src=x onerror=alert(1)>' AS value" : "print('<img src=x onerror=alert(1)>')";
    const { screen } = await renderCell({ cell: cell({ kind, source }) });
    const preview = screen.container.querySelector("[data-cell-preview]");
    expect(preview?.querySelector(".hljs-keyword, .hljs-built_in")).not.toBeNull();
    expect(preview?.textContent).toContain(source);
    expect(preview?.querySelector("img")).toBeNull();
    expect(screen.container.querySelector(".monaco-editor")).toBeNull();
  });

  it.each([false, true])("keeps preview and editor syntax legible in dark=%s", async (dark) => {
    const wasDark = document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", dark);
    try {
      const source = "-- notebook comment\nSELECT 'value', 42";
      const { screen } = await renderCell({ cell: cell({ source }) });
      const background = readToken("--card", "#ffffff");
      const preview = screen.container.querySelector("[data-cell-preview]")!;
      const syntax = Array.from(preview.querySelectorAll<HTMLElement>(".hljs > span"));
      expect(syntax.length).toBeGreaterThanOrEqual(4);
      for (const token of syntax) {
        expect(contrast(getComputedStyle(token).color, background), token.textContent ?? "").toBeGreaterThanOrEqual(4.5);
      }
      await screen.unmount();
      const editorScreen = await renderCell({ cell: cell({ source }), active: true, editing: true });
      await expect.element(editorScreen.screen.getByRole("textbox", { name: "SQL cell editor" })).toBeVisible();
      await expect.poll(() => editorScreen.screen.container.querySelectorAll(".view-line span span").length).toBeGreaterThanOrEqual(4);
      for (const token of editorScreen.screen.container.querySelectorAll<HTMLElement>(".view-line span span")) {
        if (token.textContent?.trim()) {
          expect(contrast(getComputedStyle(token).color, background), token.textContent).toBeGreaterThanOrEqual(4.5);
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
      await editorScreen.screen.unmount();
    } finally {
      document.documentElement.classList.toggle("dark", wasDark);
    }
  });

  it.each(["Control", "Meta"])("runs exactly once with %s+Enter inside the code editor", async (modifier) => {
    const onKey = vi.fn();
    const onSource = vi.fn();
    const { screen } = await renderCell({ active: true, editing: true, onKey, onSource });
    const editor = screen.getByRole("textbox", { name: "SQL cell editor" });
    await expect.element(editor).toBeVisible();
    await expect.element(editor).toHaveFocus();
    await userEvent.keyboard(`{${modifier}>}{Enter}{/${modifier}}`);
    expect(onKey).toHaveBeenCalledExactlyOnceWith("c1", "run");
    await userEvent.keyboard("{Enter}");
    expect(onKey).toHaveBeenCalledTimes(1);
    expect(onSource).toHaveBeenCalledWith("c1", expect.stringContaining("\n"));
    // Let Monaco's 50 ms word-highlight task finish before disposing its model.
    await new Promise((resolve) => setTimeout(resolve, 100));
  });

  it.each([{ canRun: false }, { run: run({ status: "running" }) }])("refuses keyboard execution while unavailable", async (props) => {
    const onKey = vi.fn();
    const { screen } = await renderCell({ ...props, onKey });
    const preview = screen.container.querySelector("[data-cell-preview]")!;
    preview.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true }));
    expect(onKey).not.toHaveBeenCalled();
  });

  it("finishes Markdown editing with Ctrl+Enter without a running runtime", async () => {
    const onKey = vi.fn();
    const { screen } = await renderCell({
      cell: cell({ kind: "markdown", source: "## Notes" }),
      active: true, editing: true, canRun: false, run: undefined, onKey,
    });
    const editor = screen.getByRole("textbox", { name: "Markdown cell editor" });
    await expect.element(editor).toHaveFocus();
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    expect(onKey).toHaveBeenCalledExactlyOnceWith("c1", "run");
    await new Promise((resolve) => setTimeout(resolve, 100));
  });

  it("opens a cell from its preview without switching editing off first", async () => {
    const onSelect = vi.fn();
    const { screen } = await renderCell({ onSelect });
    const preview = screen.container.querySelector<HTMLElement>("[data-cell-preview]")!;
    await userEvent.click(preview);
    expect(onSelect).toHaveBeenCalledWith("c1", true);
    expect(onSelect).not.toHaveBeenCalledWith("c1", false);
  });

  it("gives Markdown room for several lines and keeps editing on clicks inside", async () => {
    const onSelect = vi.fn();
    const onSource = vi.fn();
    const { screen } = await renderCell({
      cell: cell({ kind: "markdown", source: "Notes" }),
      active: true, editing: true, run: undefined, onSelect, onSource,
    });
    const editor = screen.getByRole("textbox", { name: "Markdown cell editor" });
    await expect.element(editor).toHaveFocus();
    const box = screen.container.querySelector<HTMLElement>("[data-cell-editor]")!;
    expect(box.getBoundingClientRect().height).toBeGreaterThanOrEqual(4 * 20);
    await userEvent.click(box);
    expect(onSelect).not.toHaveBeenCalledWith("c1", false);
    await userEvent.keyboard("{End}{Enter}a{Enter}b{Enter}c{Enter}d{Enter}e");
    expect(onSource).toHaveBeenLastCalledWith("c1", "Notes\na\nb\nc\nd\ne");
    await expect.poll(() => box.getBoundingClientRect().height).toBeGreaterThanOrEqual(6 * 20);
    await new Promise((resolve) => setTimeout(resolve, 100));
  });

  it("changes type directly from its labelled dropdown using the keyboard", async () => {
    const onKind = vi.fn();
    const { screen } = await renderCell({ onKind });
    const type = screen.getByRole("combobox", { name: "Cell type" });
    await expect.element(type).toHaveTextContent("SQL");
    await type.click();
    await userEvent.keyboard("{Home}");
    await expect.element(page.getByRole("option", { name: "Python", exact: true })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(onKind).toHaveBeenCalledExactlyOnceWith("c1", "python");
    await expect.element(type).toHaveFocus();
    await type.click();
    await page.getByRole("option", { name: "Markdown", exact: true }).click();
    expect(onKind).toHaveBeenLastCalledWith("c1", "markdown");
  });

  it.each([{ canEdit: false }, { run: run({ status: "running" }) }])("keeps the type visible but refuses changing it when locked", async (props) => {
    const { screen } = await renderCell(props);
    const type = screen.getByRole("combobox", { name: "Cell type" });
    await expect.element(type).toHaveTextContent("SQL");
    await expect.element(type).toBeDisabled();
  });

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

  it("runs but cannot be changed in a view-only workspace", async () => {
    const { screen, onCommand } = await renderCell({ canEdit: false });
    await screen.getByRole("button", { name: "Cell actions" }).click();
    await expect.element(page.getByRole("menuitem", { name: /^Move up/ })).toHaveAttribute("aria-disabled", "true");
    await userEvent.keyboard("{Escape}");
    await screen.getByRole("button", { name: "Run this cell" }).click();
    expect(onCommand).toHaveBeenCalledWith("c1", "run");
  });

  it("moves up and down from Cell actions", async () => {
    const { screen, onCommand } = await renderCell();
    await screen.getByRole("button", { name: "Cell actions" }).click();
    await page.getByRole("menuitem", { name: /^Move up/ }).click();
    await screen.getByRole("button", { name: "Cell actions" }).click();
    await page.getByRole("menuitem", { name: /^Move down/ }).click();
    expect(onCommand).toHaveBeenNthCalledWith(1, "c1", "move-up");
    expect(onCommand).toHaveBeenNthCalledWith(2, "c1", "move-down");
  });

  it("refuses moving beyond the first position", async () => {
    const first = await renderCell({ index: 0 });
    await first.screen.getByRole("button", { name: "Cell actions" }).click();
    await expect.element(page.getByRole("menuitem", { name: /^Move up/ })).toHaveAttribute("aria-disabled", "true");
    await userEvent.keyboard("{Escape}");
  });

  it("uses Markdown headings without a separate title or old execution controls", async () => {
    const { screen } = await renderCell({
      cell: cell({ kind: "markdown", source: "## Revenue notes", title: "Old code title" }),
      onTitle: vi.fn(), run: run({ status: "failed" }),
    });
    await expect.element(screen.getByRole("heading", { name: "Revenue notes" })).toBeVisible();
    expect(screen.container.querySelector('[aria-label="Cell title"]')).toBeNull();
    expect(screen.container.querySelector('[aria-label="Run this cell"]')).toBeNull();
    await expect.element(screen.getByRole("combobox", { name: "Cell type" })).toHaveTextContent("Markdown");
  });

  it("saves a title on Enter and keeps SQL without an engine choice", async () => {
    const onTitle = vi.fn();
    const { screen } = await renderCell({ onTitle, active: true });
    const title = screen.getByRole("textbox", { name: "Cell title" });
    await title.fill("Revenue by region");
    await title.element().dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    (title.element() as HTMLInputElement).blur();
    expect(onTitle).toHaveBeenCalledWith("c1", "Revenue by region");
    expect(screen.container.querySelector("[role=radio]")).toBeNull();
    await expect.element(screen.getByRole("textbox", { name: "Result name" })).toHaveValue("result_1");
  });

  it("offers to move an older Nova SQL cell to DuckDB", async () => {
    const onEngine = vi.fn();
    const { screen } = await renderCell({ cell: cell({ engine: "nova" }), onEngine });
    await screen.getByRole("button", { name: "Run in DuckDB" }).click();
    expect(onEngine).toHaveBeenCalledWith("c1", "duckdb");
  });
});
