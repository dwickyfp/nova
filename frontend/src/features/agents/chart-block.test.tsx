import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { ChartBlock } from "./chart-block";

const mocks = vi.hoisted(() => ({ embed: vi.fn() }));

vi.mock("vega-embed", () => ({ default: mocks.embed }));

function fakeView() {
  const view = {
    finalize: vi.fn(),
    width: vi.fn(),
    resize: vi.fn(),
    runAsync: vi.fn().mockResolvedValue(undefined),
  };
  view.width.mockReturnValue(view);
  view.resize.mockReturnValue(view);
  return view;
}

const spec = (mark: string) =>
  JSON.stringify({ mark, data: { values: [{ x: 1 }] } });

describe("ChartBlock", () => {
  beforeEach(() => {
    mocks.embed.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("recovers after an embed failure when the spec changes", async () => {
    const view = fakeView();
    mocks.embed.mockRejectedValueOnce(new Error("invalid spec"));
    mocks.embed.mockResolvedValueOnce({ view });
    const screen = await render(<ChartBlock spec={spec("bar")} />);

    await expect
      .element(screen.getByText("The chart could not be rendered."))
      .toBeVisible();
    await screen.rerender(<ChartBlock spec={spec("line")} />);

    await vi.waitFor(() => expect(mocks.embed).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => {
      expect(
        screen.getByTestId("vega-chart-container").element(),
      ).not.toHaveAttribute("hidden");
    });
  });

  it("finalizes an embed that resolves after unmount", async () => {
    const view = fakeView();
    let resolveEmbed!: (value: { view: ReturnType<typeof fakeView> }) => void;
    mocks.embed.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveEmbed = resolve;
        }),
    );
    const screen = await render(<ChartBlock spec={spec("bar")} />);

    await vi.waitFor(() => expect(mocks.embed).toHaveBeenCalledOnce());
    screen.unmount();
    resolveEmbed({ view });

    await vi.waitFor(() => expect(view.finalize).toHaveBeenCalledOnce());
  });

  it("resizes a responsive chart when its container changes", async () => {
    const view = fakeView();
    let onResize!: ResizeObserverCallback;
    const disconnect = vi.fn();
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: ResizeObserverCallback) {
          onResize = callback;
        }
        observe() {}
        disconnect() {
          disconnect();
        }
      },
    );
    mocks.embed.mockResolvedValueOnce({ view });
    const screen = await render(<ChartBlock spec={spec("bar")} />);
    await vi.waitFor(() => expect(mocks.embed).toHaveBeenCalledOnce());
    const container = screen.getByTestId("vega-chart-container").element();
    Object.defineProperty(container, "clientWidth", {
      configurable: true,
      value: 420,
    });

    onResize([], {} as ResizeObserver);

    await vi.waitFor(() => expect(view.width).toHaveBeenCalledWith(420));
    expect(view.resize).toHaveBeenCalled();
    expect(view.runAsync).toHaveBeenCalled();
    screen.unmount();
    expect(disconnect).toHaveBeenCalled();
  });
});
