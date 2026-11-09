import { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { DatavizWorkspace } from "./dataviz-workspace";

vi.mock("@/features/bi/bi-frame", () => ({
  BiFrame: forwardRef<HTMLIFrameElement, { title: string }>(function BiFrame(props, ref) {
    return <iframe ref={ref} title={props.title} srcDoc="<p>Superset</p>" />;
  }),
}));
vi.mock("../studio-chat", () => ({ StudioChat: () => <p>Assistant transcript</p> }));

async function mount() {
  return render(
    <TooltipProvider>
      <div style={{ height: 700 }}>
        <DatavizWorkspace
          onPathChange={() => {}}
          onContinueInAgent={() => {}}
          currentRole="ANALYST"
          visible
        />
      </div>
    </TooltipProvider>,
  );
}

function slide(container: HTMLElement) {
  const aside = container.querySelector<HTMLElement>("#dataviz-assistant")!;
  return { aside, wrapper: aside.parentElement as HTMLElement };
}

describe("the Dataviz assistant panel", () => {
  beforeEach(() => localStorage.removeItem("nova.dataviz.assistant"));
  afterEach(() => page.viewport(1280, 900));

  it("slides closed and open, and a closed panel is out of reach", async () => {
    await page.viewport(1440, 900);
    const screen = await mount();
    const { aside, wrapper } = slide(screen.container);
    expect(wrapper.dataset.state).toBe("open");
    expect(wrapper.style.width).toBe("400px");
    expect(wrapper.className).toContain("transition-[width]");
    expect(wrapper.className).toContain("duration-200");
    expect(wrapper.className).toContain("motion-reduce:transition-none");
    expect(aside.inert).toBe(false);

    await screen.getByRole("button", { name: "Close assistant" }).click();
    await vi.waitFor(() => expect(wrapper.dataset.state).toBe("closed"));
    expect(wrapper.style.width).toBe("0px");
    // Still mounted, so the conversation survives; inert keeps it unreachable.
    expect(aside.inert).toBe(true);
    expect(screen.getByText("Assistant transcript").query()).not.toBeNull();

    await screen.getByRole("button", { name: "Assistant", exact: true }).click();
    await vi.waitFor(() => expect(wrapper.dataset.state).toBe("open"));
    expect(wrapper.style.width).toBe("400px");
    expect(aside.inert).toBe(false);
  });

  it("slides over the dashboard on a phone", async () => {
    await page.viewport(375, 800);
    const screen = await mount();
    const { wrapper } = slide(screen.container);
    expect(wrapper.className).toContain("transition-transform");
    await screen.getByRole("button", { name: "Close assistant" }).last().click();
    await vi.waitFor(() => expect(wrapper.dataset.state).toBe("closed"));
    expect(wrapper.className).toContain("translate-x-full");
  });
});
