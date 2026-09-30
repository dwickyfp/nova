import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent, page } from "vitest/browser";
import {
  QueryConfirmationDialog,
  snapshotQuery,
  type QuerySnapshot,
} from "./query-confirmation";
import "@/styles/index.css";
import { AssistantPanel } from "@/features/assistant/assistant-panel";

const original = {
  sql: "SELECT 1; UPDATE t SET x=2",
  tabId: "a",
  database: "db",
  schema: "default",
};

function harness(onConfirm: (query: QuerySnapshot) => void) {
  function Harness() {
    const [pending, setPending] = useState<QuerySnapshot | null>(
      snapshotQuery(original),
    );
    const [assistantOpen, setAssistantOpen] = useState(true);
    return (
      <>
        <AssistantPanel open={assistantOpen} onOpenChange={setAssistantOpen} />
        <QueryConfirmationDialog
          pending={pending}
          onAssistantOpenChange={setAssistantOpen}
          onCancel={() => setPending(null)}
          onConfirm={(query) => {
            setPending(null);
            onConfirm(query);
          }}
        />
      </>
    );
  }
  return render(<Harness />);
}

describe("worksheet server confirmation", () => {
  it("keeps SQL and namespace independent of later editor changes", () => {
    const editor = { ...original };
    const snapshot = snapshotQuery(editor);
    editor.sql = "DROP TABLE other";
    editor.database = "other";
    expect(snapshot).toEqual(original);
    expect(Object.isFrozen(snapshot)).toBe(true);
  });

  it("cancels without executing SQL", async () => {
    const execute = vi.fn();
    const screen = await harness(execute);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await expect
      .element(screen.getByRole("alertdialog"))
      .not.toBeInTheDocument();
    expect(execute).not.toHaveBeenCalled();
  });

  it("confirms the original snapshot with the keyboard", async () => {
    const execute = vi.fn();
    const screen = await harness(execute);
    const button = screen.getByRole("button", { name: "Run query" });
    button.element().focus();
    await userEvent.keyboard("{Enter}");
    expect(execute).toHaveBeenCalledExactlyOnceWith(original);
  });

  it("closes with Escape and fits a narrow viewport with an adjacent assistant", async () => {
    await page.viewport(320, 640);
    const execute = vi.fn();
    const screen = await harness(execute);
    const dialog = screen.getByRole("alertdialog").element();
    const rect = dialog.getBoundingClientRect();
    expect(rect.left).toBeGreaterThanOrEqual(0);
    expect(rect.right).toBeLessThanOrEqual(320);
    await expect
      .element(screen.getByRole("dialog", { name: "Nove" }))
      .not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await expect
      .element(screen.getByRole("alertdialog"))
      .not.toBeInTheDocument();
    expect(execute).not.toHaveBeenCalled();
    await page.viewport(1280, 720);
  });

  it("keeps confirmation reachable when an open assistant becomes a mobile sheet", async () => {
    await page.viewport(1280, 720);
    const execute = vi.fn();
    const screen = await harness(execute);
    await expect
      .element(
        screen.getByRole("complementary", {
          name: "Nove",
          includeHidden: true,
        }),
      )
      .toBeInTheDocument();
    await page.viewport(320, 640);
    const dialog = screen.getByRole("alertdialog");
    await expect.element(dialog).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Run query" }));
    expect(execute).toHaveBeenCalledExactlyOnceWith(original);
    await page.viewport(1280, 720);
  });
});
