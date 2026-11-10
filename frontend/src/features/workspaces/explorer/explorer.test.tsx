import { describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import type { WorkspaceEntry } from "../types";
import type { WorkspaceSpace } from "./api";
import type { ExplorerController } from "./controller";
import { NewItemButton } from "./new-item-button";
import { childrenOf, filterEntries, freeName, moveTargets, subtree } from "./tree-data";
import "@/styles/index.css";

const entry = (path: string, type: "file" | "folder" = "file"): WorkspaceEntry => {
  const parts = path.split("/");
  return {
    id: path,
    name: parts[parts.length - 1],
    parent_path: parts.slice(0, -1).join("/"),
    path,
    entry_type: type,
    size_bytes: 0,
  };
};

const tree = [
  entry("reports", "folder"),
  entry("reports/q3", "folder"),
  entry("reports/q3/margin.sql"),
  entry("reports/q10.sql"),
  entry("reports/q2.sql"),
  entry("scratch.sql"),
  entry("Untitled.sql"),
  entry("Untitled-2.sql"),
];

describe("tree data", () => {
  it("lists folders first, then names in natural order", () => {
    expect(childrenOf(tree, "reports").map((e) => e.name)).toEqual(["q3", "q2.sql", "q10.sql"]);
  });

  it("keeps the folders that lead to a match", () => {
    expect(filterEntries(tree, "margin").map((e) => e.path)).toEqual([
      "reports",
      "reports/q3",
      "reports/q3/margin.sql",
    ]);
    expect(filterEntries(tree, "  ")).toBe(tree);
  });

  it("deletes a folder with everything under it", () => {
    expect(subtree(tree, tree[1]).map((e) => e.path)).toEqual(["reports/q3", "reports/q3/margin.sql"]);
  });

  it("never moves a folder into itself or where it already is", () => {
    expect(moveTargets(tree, tree[0])).toEqual([]);
    expect(moveTargets(tree, tree[2])).toEqual(["", "reports"]);
  });

  it("finds a free name in a folder", () => {
    expect(freeName(tree, "", "Untitled", ".sql")).toBe("Untitled-3.sql");
    expect(freeName(tree, "reports", "Untitled", ".sql")).toBe("Untitled.sql");
  });
});

const space = (permission: WorkspaceSpace["permission"]): WorkspaceSpace => ({
  id: permission === "owner" ? "personal" : "ws1",
  name: permission === "owner" ? "Personal Workspace" : "Finance",
  kind: permission === "owner" ? "personal" : "shared",
  owner: "nova_admin",
  permission,
});

function controller(permission: WorkspaceSpace["permission"], parentPath = ""): ExplorerController {
  return {
    dialog: null,
    open: vi.fn(),
    close: vi.fn(),
    place: { space: space(permission), parentPath },
    setPlace: vi.fn(),
  };
}

describe("NewItemButton", () => {
  it("creates a SQL file where the person selected, and the rest from its menu", async () => {
    const onSqlFile = vi.fn();
    const onNotebook = vi.fn();
    const ctrl = controller("owner", "reports");
    await render(<NewItemButton controller={ctrl} onSqlFile={onSqlFile} onNotebook={onNotebook} />);

    await userEvent.click(page.getByRole("button", { name: "SQL file", exact: true }));
    expect(onSqlFile).toHaveBeenCalledWith(ctrl.place);

    await userEvent.click(page.getByRole("button", { name: "More things to create" }));
    await expect.element(page.getByText("In Personal Workspace / reports")).toBeVisible();
    await userEvent.click(page.getByRole("menuitem", { name: "Notebook" }));
    expect(onNotebook).toHaveBeenCalledWith(ctrl.place);

    await userEvent.click(page.getByRole("button", { name: "More things to create" }));
    await userEvent.click(page.getByRole("menuitem", { name: "Folder" }));
    expect(ctrl.open).toHaveBeenCalledWith({ kind: "new-folder", place: ctrl.place });
  });

  it("only offers a new shared workspace in a workspace shared to view", async () => {
    const ctrl = controller("view");
    await render(<NewItemButton controller={ctrl} onSqlFile={vi.fn()} onNotebook={vi.fn()} />);
    expect(page.getByRole("button", { name: "SQL file", exact: true }).element()).toHaveProperty(
      "disabled",
      true,
    );
    await userEvent.click(page.getByRole("button", { name: "More things to create" }));
    await expect
      .element(page.getByRole("menuitem", { name: "Notebook" }))
      .toHaveAttribute("aria-disabled", "true");
    await userEvent.click(page.getByRole("menuitem", { name: "Shared workspace" }));
    expect(ctrl.open).toHaveBeenCalledWith({ kind: "new-workspace" });
  });
});
