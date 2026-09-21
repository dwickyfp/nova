import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import {
  ASSISTANT_MAX_WIDTH,
  ASSISTANT_MIN_WIDTH,
} from "./assistant-panel-state";
import { PanelResizeHandle } from "./panel-resize-handle";

describe("PanelResizeHandle", () => {
  it("exposes an accessible separator with the current width", async () => {
    const { getByRole } = await render(
      <PanelResizeHandle width={400} onResize={() => {}} />,
    );
    const handle = getByRole("separator", { name: "Resize assistant panel" });
    await expect.element(handle).toBeInTheDocument();
    expect(handle.element().getAttribute("aria-valuenow")).toBe("400");
    expect(handle.element().getAttribute("aria-valuemin")).toBe(
      String(ASSISTANT_MIN_WIDTH),
    );
    expect(handle.element().getAttribute("aria-valuemax")).toBe(
      String(ASSISTANT_MAX_WIDTH),
    );
  });

  it("widens on ArrowLeft and narrows on ArrowRight", async () => {
    const onResize = vi.fn();
    const { getByRole } = await render(
      <PanelResizeHandle width={400} onResize={onResize} />,
    );
    const handle = getByRole("separator", { name: "Resize assistant panel" });

    await handle
      .element()
      .dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }),
      );
    expect(onResize).toHaveBeenLastCalledWith(416);

    await handle
      .element()
      .dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
      );
    expect(onResize).toHaveBeenLastCalledWith(384);
  });

  it("jumps to the bounds with Home and End, clamped", async () => {
    const onResize = vi.fn();
    const { getByRole } = await render(
      <PanelResizeHandle width={400} onResize={onResize} />,
    );
    const handle = getByRole("separator", { name: "Resize assistant panel" });

    await handle
      .element()
      .dispatchEvent(
        new KeyboardEvent("keydown", { key: "Home", bubbles: true }),
      );
    expect(onResize).toHaveBeenLastCalledWith(ASSISTANT_MIN_WIDTH);

    await handle
      .element()
      .dispatchEvent(
        new KeyboardEvent("keydown", { key: "End", bubbles: true }),
      );
    expect(onResize).toHaveBeenLastCalledWith(ASSISTANT_MAX_WIDTH);
  });

  it("ignores unrelated keys", async () => {
    const onResize = vi.fn();
    const { getByRole } = await render(
      <PanelResizeHandle width={400} onResize={onResize} />,
    );
    await getByRole("separator", { name: "Resize assistant panel" })
      .element()
      .dispatchEvent(new KeyboardEvent("keydown", { key: "a", bubbles: true }));
    expect(onResize).not.toHaveBeenCalled();
  });
});
