import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { TooltipProvider } from "@/components/ui/tooltip";
import { StudioSidebar } from "./studio-sidebar";
import { relativeUpdatedAt } from "./thread-time";
import type { AgentThread } from "@/features/agents/api";

function thread(overrides: Partial<AgentThread> = {}): AgentThread {
  return {
    thread_id: "t1",
    title: "What was total revenue?",
    workspace_file_id: null,
    agent_id: "a1",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    message_count: 2,
    ...overrides,
  };
}

function renderSidebar(props: {
  threads: AgentThread[];
  onDeleteThread?: (id: string) => void;
  onRenameThread?: (id: string, title: string) => void;
  onOpenThread?: (id: string) => void;
}) {
  return render(
    <TooltipProvider>
      <StudioSidebar
        view="chat"
        onView={() => {}}
        threads={props.threads}
        activeThreadId={null}
        onOpenThread={props.onOpenThread ?? (() => {})}
        onNewChat={() => {}}
        onDeleteThread={props.onDeleteThread}
        onRenameThread={props.onRenameThread}
        open
        onToggle={() => {}}
      />
    </TooltipProvider>,
  );
}

describe("StudioSidebar", () => {
  it("treats New chat as an action and never as the active view", async () => {
    const onNewChat = vi.fn();
    const onView = vi.fn();
    const screen = await render(
      <TooltipProvider>
        <StudioSidebar
          view="chat"
          onView={onView}
          threads={[]}
          activeThreadId={null}
          onOpenThread={() => {}}
          onNewChat={onNewChat}
          open
          onToggle={() => {}}
        />
      </TooltipProvider>,
    );

    const button = screen.getByRole("button", { name: "New chat" }).first();
    await expect
      .element(screen.getByText("nova", { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByText("Studio", { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByText("Data warehouse + AI"))
      .not.toBeInTheDocument();
    await expect.element(button).not.toHaveAttribute("aria-current");
    await userEvent.click(button);

    expect(onNewChat).toHaveBeenCalledTimes(1);
    expect(onView).not.toHaveBeenCalled();
  });

  it("shows the full title in a tooltip when the row truncates it", async () => {
    const title = "Show revenue by product category for the last twelve months";
    const screen = await renderSidebar({ threads: [thread({ title })] });

    // The visible line truncates; the tooltip is the way to read it in full.
    await userEvent.hover(screen.getByText(title));
    await expect
      .poll(() => screen.getByText(title).elements().length)
      .toBeGreaterThan(0);
  });

  it("renders the conversation as a two-line row with its relative date", async () => {
    const screen = await renderSidebar({ threads: [thread()] });

    await expect.element(screen.getByText("History")).toBeVisible();
    await expect
      .element(screen.getByText("What was total revenue?"))
      .toBeVisible();
    await expect.element(screen.getByText("now")).toBeVisible();
  });

  it("exposes rename and delete only when the host can manage threads", async () => {
    const screen = await renderSidebar({ threads: [thread()] });

    await expect
      .element(
        screen.getByRole("button", { name: "Rename What was total revenue?" }),
      )
      .not.toBeInTheDocument();
    await expect
      .element(
        screen.getByRole("button", { name: "Delete What was total revenue?" }),
      )
      .not.toBeInTheDocument();
  });

  it("renames in place and reports the trimmed title", async () => {
    const onRenameThread = vi.fn();
    const screen = await renderSidebar({
      threads: [thread()],
      onRenameThread,
    });

    await userEvent.click(
      screen.getByRole("button", { name: "Rename What was total revenue?" }),
    );
    const input = screen.getByRole("textbox", { name: "Conversation title" });
    await userEvent.clear(input);
    await userEvent.type(input, "  Revenue check  ");
    await userEvent.keyboard("{Enter}");

    expect(onRenameThread).toHaveBeenCalledWith("t1", "Revenue check");
  });

  it("deletes from the row action", async () => {
    const onDeleteThread = vi.fn();
    const screen = await renderSidebar({
      threads: [thread()],
      onDeleteThread,
    });

    await userEvent.click(
      screen.getByRole("button", { name: "Delete What was total revenue?" }),
    );

    expect(onDeleteThread).toHaveBeenCalledWith("t1");
  });
});

describe("relativeUpdatedAt", () => {
  it("prefers a clock time within today and a short date beyond it", () => {
    const now = new Date();
    expect(relativeUpdatedAt(now.toISOString())).not.toBe("");

    const old = new Date(now.getTime() - 5 * 86_400_000);
    expect(relativeUpdatedAt(old.toISOString())).toMatch(/[A-Za-z]{3}/);
  });

  it("returns an empty string for an unparseable timestamp", () => {
    expect(relativeUpdatedAt("not-a-date")).toBe("");
  });
});
