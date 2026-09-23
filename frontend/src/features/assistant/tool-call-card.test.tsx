import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { ToolCallCard } from "./tool-call-card";
import type { ToolCallView } from "./types";

const readOnly: ToolCallView = {
  tool_name: "query_execute",
  sql_preview: "SELECT region, SUM(amount) FROM sales GROUP BY region",
  classification: "read_only",
  status: "pending",
};

const destructive: ToolCallView = {
  ...readOnly,
  sql_preview: "DROP TABLE sales",
  classification: "destructive",
};

describe("ToolCallCard", () => {
  it("shows the SQL preview and a textual status label", async () => {
    const { getByText } = await render(
      <ToolCallCard
        toolCall={readOnly}
        toolCallId="tc-1"
        onDecide={() => {}}
      />,
    );
    await expect
      .element(
        getByText("SELECT region, SUM(amount) FROM sales GROUP BY region"),
      )
      .toBeInTheDocument();
    await expect.element(getByText("Awaiting approval")).toBeInTheDocument();
  });

  it("offers allow, deny and always-allow for a read-only statement", async () => {
    const onDecide = vi.fn();
    const { getByRole, getByText } = await render(
      <ToolCallCard
        toolCall={readOnly}
        toolCallId="tc-1"
        onDecide={onDecide}
      />,
    );
    await getByRole("checkbox").click();
    await getByRole("button", { name: "Allow" }).click();
    expect(onDecide).toHaveBeenCalledWith({
      toolCallId: "tc-1",
      decision: "approve",
      alwaysAllow: true,
    });
    await expect
      .element(getByText("Always allow read-only queries in this conversation"))
      .toBeInTheDocument();
  });

  it("never offers always-allow for a destructive statement", async () => {
    const onDecide = vi.fn();
    const { getByRole, container } = await render(
      <ToolCallCard
        toolCall={destructive}
        toolCallId="tc-1"
        onDecide={onDecide}
      />,
    );
    expect(container.querySelector('[data-slot="checkbox"]')).toBeNull();
    await getByRole("button", { name: "Allow" }).click();
    expect(onDecide).toHaveBeenCalledWith({
      toolCallId: "tc-1",
      decision: "approve",
      alwaysAllow: false,
    });
  });

  it("collects a new-user password only when the user approves", async () => {
    const onDecide = vi.fn();
    const { getByLabelText, getByRole } = await render(
      <ToolCallCard
        toolCall={{
          ...destructive,
          tool_name: "call_ui_operation",
          sql_preview: 'POST /api/v1/users\n{"body":{"username":"maya"}}',
        }}
        toolCallId="create-user"
        onDecide={onDecide}
      />,
    );
    const password = getByLabelText("Password for the new user");
    await password.fill("private-marker");
    await getByRole("button", { name: "Allow" }).click();
    expect(onDecide).toHaveBeenCalledWith({
      toolCallId: "create-user",
      decision: "approve",
      alwaysAllow: false,
      secureInput: { password: "private-marker" },
    });
    await expect.element(password).toHaveValue("");
  });

  it("keeps a stage upload file out of the preview and sends it on approval", async () => {
    const onDecide = vi.fn();
    const { getByLabelText, getByRole } = await render(
      <ToolCallCard
        toolCall={{
          ...destructive,
          tool_name: "call_ui_operation",
          sql_preview:
            'POST /api/v1/stages/stage-1/files\n{"file":"Choose a local file"}',
        }}
        toolCallId="stage-upload"
        onDecide={onDecide}
      />,
    );
    const file = new File(["private-file-data"], "data.csv", {
      type: "text/csv",
    });
    await userEvent.upload(getByLabelText("File to upload"), file);
    await getByRole("button", { name: "Allow" }).click();
    expect(onDecide).toHaveBeenCalledWith({
      toolCallId: "stage-upload",
      decision: "approve",
      alwaysAllow: false,
      uploadFile: file,
    });
  });

  it("offers a file chooser for explorer stage uploads", async () => {
    const { getByLabelText } = await render(
      <ToolCallCard
        toolCall={{
          ...destructive,
          tool_name: "call_ui_operation",
          sql_preview:
            'POST /api/v1/explorer/databases/sales/stages/incoming/files\n{"body":{"filename":"reports/data.csv"}}',
        }}
        toolCallId="explorer-upload"
        onDecide={() => {}}
      />,
    );
    await expect.element(getByLabelText("File to upload")).toBeInTheDocument();
  });

  it("sends a deny decision", async () => {
    const onDecide = vi.fn();
    const { getByRole } = await render(
      <ToolCallCard
        toolCall={readOnly}
        toolCallId="tc-1"
        onDecide={onDecide}
      />,
    );
    await getByRole("button", { name: "Deny" }).click();
    expect(onDecide).toHaveBeenCalledWith({
      toolCallId: "tc-1",
      decision: "deny",
      alwaysAllow: false,
    });
  });

  it("hides approval controls once the call is no longer pending", async () => {
    const { container } = await render(
      <ToolCallCard
        toolCall={{ ...readOnly, status: "done", result_summary: "42 rows" }}
        toolCallId="tc-1"
        onDecide={() => {}}
      />,
    );
    expect(container.textContent).toContain("42 rows");
    expect(container.textContent).not.toContain("Deny");
  });

  it("does not offer a decision while the tool call has no server id", async () => {
    const { container } = await render(
      <ToolCallCard toolCall={readOnly} onDecide={() => {}} />,
    );
    expect(container.textContent).not.toContain("Deny");
  });

  it("surfaces a tool error with alert semantics", async () => {
    const { getByRole } = await render(
      <ToolCallCard
        toolCall={{
          ...readOnly,
          status: "failed",
          error: "Permission denied by engine",
        }}
        toolCallId="tc-1"
        onDecide={() => {}}
      />,
    );
    await expect
      .element(getByRole("alert"))
      .toHaveTextContent("Permission denied by engine");
  });
});
