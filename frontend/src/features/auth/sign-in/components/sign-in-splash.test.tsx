import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { page } from "vitest/browser";
import "@/styles/index.css";
import { SignInSplash } from "./sign-in-splash";

afterEach(async () => {
  await cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  document.documentElement.style.fontSize = "";
  await page.viewport(1280, 720);
});

describe("Sign-in splash", () => {
  it("covers the viewport, takes focus, and finishes after two wingbeats", async () => {
    await page.viewport(320, 480);
    vi.useFakeTimers();
    const onReady = vi.fn();
    const screen = await render(<SignInSplash onReady={onReady} />);
    const status = screen
      .getByRole("status", { name: "Opening Nova" })
      .element();
    const bounds = status.getBoundingClientRect();
    expect(bounds.width).toBe(window.innerWidth);
    expect(bounds.height).toBe(window.innerHeight);
    expect(status.scrollWidth).toBe(status.clientWidth);
    expect(document.activeElement).toBe(status);
    document.documentElement.style.fontSize = "200%";
    for (const child of status.children) {
      const rect = child.getBoundingClientRect();
      expect(rect.top).toBeGreaterThanOrEqual(0);
      expect(rect.bottom).toBeLessThanOrEqual(window.innerHeight);
    }
    await vi.advanceTimersByTimeAsync(1599);
    expect(onReady).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(onReady).toHaveBeenCalledOnce();
  });

  it("shortens the transition for reduced motion", async () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: true,
    } as MediaQueryList);
    vi.useFakeTimers();
    const onReady = vi.fn();
    await render(<SignInSplash onReady={onReady} />);
    await vi.advanceTimersByTimeAsync(200);
    expect(onReady).toHaveBeenCalledOnce();
  });

  it("cancels pending navigation when the splash unmounts", async () => {
    vi.useFakeTimers();
    const onReady = vi.fn();
    const screen = await render(<SignInSplash onReady={onReady} />);
    await screen.unmount();
    await vi.advanceTimersByTimeAsync(2000);
    expect(onReady).not.toHaveBeenCalled();
  });
});
