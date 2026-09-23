import { page } from "vitest/browser";
import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { AssistantPanel } from "./assistant-panel";

describe("AssistantPanel", () => {
  it("renders nothing inline when closed", async () => {
    const { container } = await render(
      <AssistantPanel open={false} onOpenChange={() => {}} />,
    );
    expect(container.textContent).toBe("");
  });

  it("renders inline as a labelled aside on a wide viewport", async () => {
    await page.viewport(1440, 900);
    try {
      const { getByRole } = await render(
        <AssistantPanel open onOpenChange={() => {}} />,
      );
      const panel = getByRole("complementary", { name: "Nove" });
      await expect.element(panel).toBeInTheDocument();
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("renders the shell with the empty state when open", async () => {
    const { getByText, getByRole } = await render(
      <AssistantPanel open onOpenChange={() => {}} />,
    );
    await expect.element(getByText("How can I help?")).toBeInTheDocument();
    await expect
      .element(getByRole("button", { name: "Close assistant" }))
      .toBeInTheDocument();
  });

  it("renders caller-supplied transcript content in place of the empty state", async () => {
    const { getByText, container } = await render(
      <AssistantPanel open onOpenChange={() => {}}>
        <p>SELECT 1</p>
      </AssistantPanel>,
    );
    await expect.element(getByText("SELECT 1")).toBeInTheDocument();
    expect(container.textContent).not.toContain("How can I help?");
  });

  it("submits a trimmed message and clears the composer", async () => {
    const onSendMessage = vi.fn();
    const { getByPlaceholder, getByRole } = await render(
      <AssistantPanel
        open
        onOpenChange={() => {}}
        onSendMessage={onSendMessage}
      />,
    );
    const field = getByPlaceholder("Ask a question or describe a query");
    await field.fill("  how many rows?  ");
    await getByRole("button", { name: "Send message" }).click();

    expect(onSendMessage).toHaveBeenCalledWith("how many rows?");
    await expect.element(field).toHaveValue("");
  });

  it("keeps Send disabled until the message has content", async () => {
    const onSendMessage = vi.fn();
    const { getByRole, getByPlaceholder } = await render(
      <AssistantPanel
        open
        onOpenChange={() => {}}
        onSendMessage={onSendMessage}
      />,
    );
    const send = getByRole("button", { name: "Send message" });
    await expect.element(send).toBeDisabled();

    await getByPlaceholder("Ask a question or describe a query").fill("   ");
    await expect.element(send).toBeDisabled();
    expect(onSendMessage).not.toHaveBeenCalled();
  });

  it("closes via the header control", async () => {
    const onOpenChange = vi.fn();
    const { getByRole } = await render(
      <AssistantPanel open onOpenChange={onOpenChange} />,
    );
    await getByRole("button", { name: "Close assistant" }).click();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("labels the header with the given title instead of the default", async () => {
    const { getByText } = await render(
      <AssistantPanel open onOpenChange={() => {}} title="sales.sql" />,
    );
    await expect.element(getByText("sales.sql")).toBeInTheDocument();
  });

  it("renders a seamless header without a bottom divider", async () => {
    await page.viewport(1440, 900);
    try {
      const { getByRole } = await render(
        <AssistantPanel open onOpenChange={() => {}} />,
      );
      const header = getByRole("heading", { name: "Nove" }).element()
        .parentElement as HTMLElement;
      expect(header.className).not.toContain("border-b");
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("uses the Nova mark in the header", async () => {
    await render(
      <AssistantPanel open onOpenChange={() => {}} />,
    );

    const logo = document.querySelector<HTMLImageElement>(
      'img[src="/images/nova-mark.svg"]',
    );
    expect(logo).not.toBeNull();
    expect(logo?.getAttribute("aria-hidden")).toBe("true");
  });

  it("gives every header action the same visual slot", async () => {
    const { getByRole } = await render(
      <AssistantPanel
        open
        onOpenChange={() => {}}
        onNewChat={() => {}}
      />,
    );
    const newChat = getByRole("button", { name: "New chat" }).element();
    const close = getByRole("button", { name: "Close assistant" }).element();
    const protection = close.parentElement?.querySelector<HTMLElement>(
      '[aria-label="Enterprise data protection"]',
    );

    expect(protection).not.toBeNull();
    expect(protection?.classList.contains("size-9")).toBe(true);
    expect(newChat.classList.contains("size-9")).toBe(true);
    expect(close.classList.contains("size-9")).toBe(true);
  });

  it("greets the signed-in user in the empty state", async () => {
    const { getByText } = await render(
      <AssistantPanel open onOpenChange={() => {}} userName="Dwicky" />,
    );
    await expect.element(getByText("Hi Dwicky,")).toBeInTheDocument();
  });

  it("offers New chat in the header and fires it", async () => {
    const onNewChat = vi.fn();
    const { getByRole } = await render(
      <AssistantPanel open onOpenChange={() => {}} onNewChat={onNewChat} />,
    );
    await getByRole("button", { name: "New chat" }).click();
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("shows no history/new-chat controls when the panel has no handlers", async () => {
    // A bare panel (no provider wiring) still renders the shell without
    // reaching for data it cannot fetch.
    const { getByRole, container } = await render(
      <AssistantPanel open onOpenChange={() => {}} />,
    );
    await expect
      .element(getByRole("button", { name: "Close assistant" }))
      .toBeInTheDocument();
    expect(container.querySelector('button[aria-label="New chat"]')).toBeNull();
    expect(
      container.querySelector('button[aria-label="Chat history"]'),
    ).toBeNull();
  });

  it("renders transcript messages instead of the empty state", async () => {
    const { getByText, container } = await render(
      <AssistantPanel
        open
        onOpenChange={() => {}}
        messages={[
          {
            message_id: "m1",
            role: "assistant",
            content: "Revenue is grouped by region.",
            tool_call: null,
            created_at: "2026-09-18T00:00:00Z",
            turn_state: "done",
          },
        ]}
      />,
    );
    await expect
      .element(getByText("Revenue is grouped by region."))
      .toBeInTheDocument();
    expect(container.textContent).not.toContain("How can I help?");
  });

  it("swaps Send for Stop while streaming and fires onStop", async () => {
    const onStop = vi.fn();
    const { getByRole } = await render(
      <AssistantPanel open onOpenChange={() => {}} streaming onStop={onStop} />,
    );
    await getByRole("button", { name: "Stop generating" }).click();
    expect(onStop).toHaveBeenCalled();
  });

  it("states that the assistant backend is not connected instead of offering a dead send", async () => {
    const { getByText, getByRole } = await render(
      <AssistantPanel open onOpenChange={() => {}} disabled />,
    );
    await expect
      .element(
        getByText(
          "The assistant backend is not connected yet. This panel is read-only until it is.",
        ),
      )
      .toBeInTheDocument();
    await expect
      .element(getByRole("button", { name: "Send message" }))
      .toBeDisabled();
  });

  it("does not show a permissions banner", async () => {
    const { getByRole } = await render(
      <AssistantPanel
        open
        onOpenChange={() => {}}
        approvalMode="allow_read_only"
      />,
    );
    expect(
      getByRole("button", { name: "Reset permissions" }).query(),
    ).toBeNull();
    expect(document.body.textContent).not.toContain("Reset permissions");
    expect(document.body.textContent).not.toContain(
      "Read-only queries are allowed in this conversation.",
    );
  });

  it("applies the given width inline and shows the resize handle", async () => {
    await page.viewport(1440, 900);
    try {
      const { getByRole, container } = await render(
        <AssistantPanel
          open
          onOpenChange={() => {}}
          width={512}
          onResize={() => {}}
        />,
      );
      const aside = container.querySelector("#assistant-panel") as HTMLElement;
      expect(aside.style.width).toBe("512px");
      await expect
        .element(getByRole("separator", { name: "Resize assistant panel" }))
        .toBeInTheDocument();
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("omits the resize handle when no resize handler is given", async () => {
    await page.viewport(1440, 900);
    try {
      const { container } = await render(
        <AssistantPanel open onOpenChange={() => {}} />,
      );
      expect(
        container.querySelector('[aria-label="Resize assistant panel"]'),
      ).toBeNull();
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("follows the pointer during a drag and commits the clamped width", async () => {
    await page.viewport(1440, 900);
    const onResize = vi.fn();
    try {
      const { getByRole, container } = await render(
        <AssistantPanel
          open
          onOpenChange={() => {}}
          width={400}
          onResize={onResize}
        />,
      );
      const handle = getByRole("separator", {
        name: "Resize assistant panel",
      }).element();
      const aside = container.querySelector("#assistant-panel") as HTMLElement;

      handle.dispatchEvent(
        new PointerEvent("pointerdown", { clientX: 1000, bubbles: true }),
      );
      // Drag left by 80px: a right-anchored panel widens to 480 while dragging.
      window.dispatchEvent(new PointerEvent("pointermove", { clientX: 920 }));
      await vi.waitFor(() => expect(aside.style.width).toBe("480px"));

      window.dispatchEvent(new PointerEvent("pointerup", { clientX: 920 }));
      await vi.waitFor(() => expect(onResize).toHaveBeenCalledWith(480));
    } finally {
      await page.viewport(375, 800);
    }
  });
});
