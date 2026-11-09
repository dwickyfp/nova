import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { AssistantComposer } from "./assistant-composer";

describe("AssistantComposer", () => {
  it("submits a trimmed message and clears the field", async () => {
    const onSendMessage = vi.fn();
    const { getByPlaceholder, getByRole } = await render(
      <AssistantComposer onSendMessage={onSendMessage} />,
    );
    const field = getByPlaceholder("Ask a question or describe a query");
    await field.fill("  how many rows?  ");
    await getByRole("button", { name: "Send message" }).click();

    expect(onSendMessage).toHaveBeenCalledWith("how many rows?");
    await expect.element(field).toHaveValue("");
  });

  it("sends on Enter but not on Shift+Enter", async () => {
    const onSendMessage = vi.fn();
    const { getByPlaceholder } = await render(
      <AssistantComposer onSendMessage={onSendMessage} />,
    );
    const field = getByPlaceholder("Ask a question or describe a query");

    await field.fill("first line");
    await field.element().dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Enter",
        shiftKey: true,
        bubbles: true,
      }),
    );
    expect(onSendMessage).not.toHaveBeenCalled();

    await field
      .element()
      .dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
      );
    expect(onSendMessage).toHaveBeenCalledWith("first line");
  });

  it("swaps Send for Stop while streaming", async () => {
    const onStop = vi.fn();
    const { getByRole } = await render(
      <AssistantComposer onSendMessage={() => {}} streaming onStop={onStop} />,
    );
    await getByRole("button", { name: "Stop generating" }).click();
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("shows the approval mode and reports a change", async () => {
    const onSelectApprovalMode = vi.fn();
    const { getByRole } = await render(
      <AssistantComposer
        onSendMessage={() => {}}
        approvalMode="ask"
        onSelectApprovalMode={onSelectApprovalMode}
      />,
    );

    const trigger = getByRole("combobox", {
      name: "Approval mode: Need approval",
    });
    await trigger.click();
    await getByRole("option", { name: /Always allow read-only/ }).click();

    expect(onSelectApprovalMode).toHaveBeenCalledWith("allow_read_only");
  });

  it("hides the approval selector when the mode or handler is absent", async () => {
    const { container } = await render(
      <AssistantComposer onSendMessage={() => {}} />,
    );
    expect(container.querySelector('[aria-label^="Approval mode"]')).toBeNull();
  });

  it("attaches a file, shows it, and sends it with the message", async () => {
    const onSendMessage = vi.fn();
    const { getByPlaceholder, getByRole, getByLabelText, getByText } = await render(
      <AssistantComposer onSendMessage={onSendMessage} />,
    );
    const input = getByLabelText("Choose files to attach").element() as HTMLInputElement;
    const transfer = new DataTransfer();
    transfer.items.add(new File(["city,orders\nJakarta,12\n"], "orders.csv", { type: "text/csv" }));
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await expect.element(getByText("orders.csv")).toBeVisible();

    await getByPlaceholder("Ask a question or describe a query").fill("How many orders?");
    await getByRole("button", { name: "Send message" }).click();
    expect(onSendMessage).toHaveBeenCalledWith("How many orders?", [
      { name: "orders.csv", content: "city,orders\nJakarta,12\n", mediaType: "text/plain" },
    ]);
    expect(getByText("orders.csv").query()).toBeNull();
  });

  it("refuses a file type Neva cannot read and says why", async () => {
    const onSendMessage = vi.fn();
    const { getByLabelText, getByRole } = await render(
      <AssistantComposer onSendMessage={onSendMessage} />,
    );
    const input = getByLabelText("Choose files to attach").element() as HTMLInputElement;
    const transfer = new DataTransfer();
    transfer.items.add(new File(["x"], "setup.exe"));
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await expect.element(getByRole("alert")).toHaveTextContent("unsupported file type");
    await expect.element(getByRole("button", { name: "Send message" })).toBeDisabled();
  });
});
