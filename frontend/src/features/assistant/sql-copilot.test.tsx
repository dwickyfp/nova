import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { Markdown } from "./markdown";
import { CodeCard } from "./code-card";

afterEach(() => vi.restoreAllMocks());

function response(results: object[]) {
  return new Response(JSON.stringify(results), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("SQL copilot execution", () => {
  it("preserves Run state and results when application events rerender Markdown", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        response([
          {
            success: true,
            columns: ["answer"],
            rows: [[42]],
            row_count: 1,
            elapsed_ms: 3,
            warnings: [],
          },
        ]),
      );
    function Surface() {
      const [version, setVersion] = useState(0);
      return (
        <Markdown
          runContext={{ database: "analytics" }}
          onExecutionEvent={() => setVersion((version) => version + 1)}
          className={`revision-${version}`}
        >
          {"```sql\nSELECT 42 AS answer;\n```"}
        </Markdown>
      );
    }
    const screen = await render(<Surface />);
    await screen.getByRole("button", { name: "Run statement" }).click();
    await expect
      .element(screen.getByText("Success", { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByRole("cell", { name: "42" }))
      .toBeVisible();
    expect(fetch).toHaveBeenCalledTimes(1);
    await expect
      .element(screen.getByText("Not run", { exact: true }))
      .not.toBeInTheDocument();
  });

  it("collects a password privately and leaves SQL, callbacks and clipboard text credential-free", async () => {
    const code =
      "CREATE USER 'audit.user' IDENTIFIED BY '<temporary_password>';\nALTER USER 'audit.user' REQUIRE PASSWORD CHANGE;";
    const execution = vi.fn();
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        response([
          {
            success: true,
            affected_rows: 1,
            row_count: 0,
            elapsed_ms: 4,
            warnings: [],
          },
        ]),
      );
    const screen = await render(
      <CodeCard
        code={code}
        language="sql"
        highlighted={null}
        runnable
        runContext={{}}
        onExecutionEvent={execution}
      />,
    );
    await screen.getByRole("button", { name: "Run statement" }).click();
    expect(fetch).not.toHaveBeenCalled();
    await screen.getByLabelText("Temporary password", { exact: true }).fill("test-only'p\\x");
    await screen.getByRole("button", { name: "Confirm and run" }).click();
    await expect
      .element(screen.getByText("Success", { exact: true }))
      .toBeVisible();
    const payload = JSON.parse(String(fetch.mock.calls[0][1]?.body));
    expect(payload.sql).toBe(code);
    expect(payload.temporary_password).toBe("test-only'p\\x");
    expect(JSON.stringify(execution.mock.calls)).not.toContain("test-only");
    await expect.element(screen.getByRole("dialog")).not.toBeInTheDocument();
    expect(screen.container.textContent).not.toContain("test-only");
  });

  it("cancels confirmation with Escape and executes destructive SQL only after confirmation", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        response([
          {
            success: true,
            affected_rows: 1,
            row_count: 0,
            elapsed_ms: 4,
            warnings: [],
          },
        ]),
      );
    const screen = await render(
      <CodeCard
        code="DELETE FROM analytics.accounts WHERE account_id=7"
        language="sql"
        highlighted={null}
        runnable
        runContext={{}}
      />,
    );
    await screen.getByRole("button", { name: "Run statement" }).click();
    await userEvent.keyboard("{Escape}");
    expect(fetch).not.toHaveBeenCalled();
    await screen.getByRole("button", { name: "Run statement" }).click();
    await screen.getByRole("button", { name: "Confirm and run" }).click();
    await expect
      .element(screen.getByText("Success", { exact: true }))
      .toBeVisible();
    expect(
      JSON.parse(String(fetch.mock.calls[0][1]?.body)).confirm_destructive,
    ).toBe(true);
  });

  it("shows unresolved placeholders as an actionable failure without submitting SQL", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const screen = await render(
      <CodeCard
        code="SELECT * FROM <table>"
        language="sql"
        highlighted={null}
        runnable
        runContext={{}}
      />,
    );
    await screen.getByRole("button", { name: "Run statement" }).click();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("Replace the remaining SQL placeholders");
    expect(fetch).not.toHaveBeenCalled();
  });
});
