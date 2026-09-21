import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { TooltipProvider } from "@/components/ui/tooltip";
import { StudioSidebar } from "./studio-sidebar";

describe("StudioSidebar", () => {
  it("treats New chat as an action and never as the active view", async () => {
    const onNewChat = vi.fn();
    const onView = vi.fn();
    const screen = await render(
      <TooltipProvider>
        <StudioSidebar
          view="chat"
          onView={onView}
          onOpenSettings={() => {}}
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
    await expect.element(screen.getByText("nova", { exact: true })).toBeVisible();
    await expect.element(screen.getByText("Studio", { exact: true })).toBeVisible();
    await expect
      .element(screen.getByText("Data warehouse + AI"))
      .not.toBeInTheDocument();
    await expect.element(button).not.toHaveAttribute("aria-current");
    await userEvent.click(button);

    expect(onNewChat).toHaveBeenCalledTimes(1);
    expect(onView).not.toHaveBeenCalled();
  });
});
