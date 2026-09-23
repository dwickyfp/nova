import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { WorkspaceTabStrip } from "./workspace-tab-strip";
import type { WorkspaceTabState } from "./types";
import "@/styles/index.css";

function tab(id: string): WorkspaceTabState {
  return {
    id,
    title: `${id}.sql`,
    content: "SELECT 1",
    savedContent: "SELECT 1",
    database: "default",
    schema: "default",
    role: "",
    loaded: true,
  };
}

function renderTabs(
  overrides: {
    onRename?: (id: string, name: string) => Promise<boolean>;
    onReorder?: (fromId: string, toId: string) => void;
  } = {},
) {
  function TabHarness() {
    const [tabs, setTabs] = useState<Record<string, WorkspaceTabState>>({
      a: tab("a"),
      b: tab("b"),
      c: tab("c"),
    });
    return (
      <WorkspaceTabStrip
        tabs={tabs}
        openTabIds={["a", "b", "c"]}
        activeTabId="a"
        onActivate={() => {}}
        onClose={() => {}}
        onReorder={overrides.onReorder ?? (() => {})}
        onRename={async (id, name) => {
          const saved = await (overrides.onRename?.(id, name) ??
            Promise.resolve(true));
          if (saved)
            setTabs((current) => ({
              ...current,
              [id]: { ...current[id], title: name },
            }));
          return saved;
        }}
        onNewFile={() => {}}
      />
    );
  }
  return render(<TabHarness />);
}

describe("WorkspaceTabStrip", () => {
  it("keeps the tab shell and width while editing its name", async () => {
    const onRename = vi.fn(async () => true);
    const screen = await renderTabs({ onRename });
    const tabButton = screen
      .getByRole("button", { name: "a.sql", exact: true })
      .element();
    const shell = tabButton.parentElement!;
    const width = shell.getBoundingClientRect().width;

    const options = screen.getByRole("button", { name: "Options for a.sql" });
    expect(
      options
        .element()
        .querySelector("svg")
        ?.classList.contains("lucide-ellipsis-vertical"),
    ).toBe(true);
    const dots = options
      .element()
      .querySelector("svg")!
      .getBoundingClientRect();
    const close = screen
      .getByRole("button", { name: "Close a.sql" })
      .element()
      .querySelector("svg")!
      .getBoundingClientRect();
    expect(
      Math.abs((dots.top + dots.bottom) / 2 - (close.top + close.bottom) / 2),
    ).toBeLessThan(0.5);
    await userEvent.click(options);
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));

    const input = screen.getByRole("textbox", { name: "Rename a.sql" });
    await expect.element(input).toBeVisible();
    expect(shell.contains(input.element())).toBe(true);
    expect(Math.abs(shell.getBoundingClientRect().width - width)).toBeLessThan(
      1,
    );
    expect(
      getComputedStyle(input.element().parentElement!).borderTopStyle,
    ).toBe("solid");

    await userEvent.clear(input);
    await userEvent.type(input, "report.sql");
    vi.spyOn(input.element(), "blur").mockImplementation(() => {});
    await userEvent.keyboard("{Enter}");
    await vi.waitFor(() => expect(onRename).toHaveBeenCalledOnce());
    expect(onRename).toHaveBeenCalledWith("a", "report.sql");
    await expect
      .element(screen.getByRole("button", { name: "report.sql", exact: true }))
      .toBeVisible();
  });

  it("moves the dragged tab smoothly before committing the new order", async () => {
    const onReorder = vi.fn();
    const screen = await renderTabs({ onReorder });
    const button = screen
      .getByRole("button", { name: "a.sql", exact: true })
      .element();
    const shell = button.parentElement!;
    const first = shell.getBoundingClientRect();
    const third = screen
      .getByRole("button", { name: "c.sql", exact: true })
      .element()
      .getBoundingClientRect();
    vi.spyOn(button, "setPointerCapture").mockImplementation(() => {});

    button.dispatchEvent(
      new PointerEvent("pointerdown", {
        bubbles: true,
        isPrimary: true,
        button: 0,
        pointerId: 1,
        clientX: first.left + first.width / 2,
      }),
    );
    button.dispatchEvent(
      new PointerEvent("pointermove", {
        bubbles: true,
        isPrimary: true,
        pointerId: 1,
        clientX: third.right + 20,
      }),
    );
    await vi.waitFor(() =>
      expect(shell.style.transform).toContain("translate3d"),
    );
    expect(onReorder).not.toHaveBeenCalled();

    button.dispatchEvent(
      new PointerEvent("pointerup", {
        bubbles: true,
        isPrimary: true,
        pointerId: 1,
        clientX: third.right + 20,
      }),
    );
    await vi.waitFor(() => expect(onReorder).toHaveBeenCalledWith("a", "c"));
  });

  it("supports keyboard reordering", async () => {
    const onReorder = vi.fn();
    const screen = await renderTabs({ onReorder });
    await userEvent.click(
      screen.getByRole("button", { name: "b.sql", exact: true }),
    );
    await userEvent.keyboard("{Alt>}{ArrowLeft}{/Alt}");
    expect(onReorder).toHaveBeenCalledWith("b", "a");
  });
});
