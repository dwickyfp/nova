import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import {
  buildRewriteHunks,
  type ProposedRewrite,
} from "@/features/assistant/query-attach";
import { rewriteBlocks } from "./rewrite-blocks";
import { RewriteReview } from "./rewrite-review";

function proposal(before: string, after: string): ProposedRewrite {
  return {
    attachmentId: "a",
    tabId: "t",
    sourceContent: before,
    sql: after,
    hunks: buildRewriteHunks(before, after),
    sourceMessageId: "neva-1",
  };
}

describe("rewriteBlocks", () => {
  it("shows the replaced lines above the new ones, between unchanged lines shown once", () => {
    const blocks = rewriteBlocks(
      proposal(
        "SELECT a\nFROM t\nGROUP BY a\nORDER BY a",
        "SELECT a, b\nFROM t\nGROUP BY a, b\nORDER BY a",
      ),
    );
    expect(blocks.map((block) => block.rows)).toEqual([
      [
        { kind: "removed", old: 1, text: "SELECT a" },
        { kind: "added", new: 1, text: "SELECT a, b" },
        { kind: "context", old: 2, new: 2, text: "FROM t" },
      ],
      [
        { kind: "removed", old: 3, text: "GROUP BY a" },
        { kind: "added", new: 3, text: "GROUP BY a, b" },
        { kind: "context", old: 4, new: 4, text: "ORDER BY a" },
      ],
    ]);
  });

  it("numbers lines after an addition in the new file", () => {
    const blocks = rewriteBlocks(
      proposal("SELECT a\nFROM t\nWHERE x", "SELECT a\nJOIN u\nFROM t\nWHERE y"),
    );
    expect(blocks.map((block) => block.rows)).toEqual([
      [
        { kind: "context", old: 1, new: 1, text: "SELECT a" },
        { kind: "added", new: 2, text: "JOIN u" },
        { kind: "context", old: 2, new: 3, text: "FROM t" },
      ],
      [
        { kind: "removed", old: 3, text: "WHERE x" },
        { kind: "added", new: 4, text: "WHERE y" },
      ],
    ]);
    // Each block is labelled with its line in the suggested SQL.
    expect(blocks.map((block) => block.line)).toEqual([2, 4]);
  });
});

describe("RewriteReview", () => {
  it("shows the counts and hands each choice back", async () => {
    const onCancel = vi.fn();
    const onApply = vi.fn();
    const onApplyAndRun = vi.fn();
    const screen = await render(
      <RewriteReview
        rewrite={proposal("SELECT 1", "SELECT 1\nLIMIT 10")}
        onCancel={onCancel}
        onApply={onApply}
        onApplyAndRun={onApplyAndRun}
      />,
    );
    await expect
      .element(screen.getByRole("region", { name: "Suggested change from Neva" }))
      .toBeVisible();
    await expect.element(screen.getByText("+1")).toBeVisible();
    await expect.element(screen.getByText("−0")).toBeVisible();
    await screen.getByRole("button", { name: "Apply & Run" }).click();
    await screen.getByRole("button", { name: "Apply", exact: true }).click();
    await screen.getByRole("button", { name: "Cancel" }).click();
    expect(onApplyAndRun).toHaveBeenCalledOnce();
    expect(onApply).toHaveBeenCalledOnce();
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
