import { page } from "vitest/browser";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AssistantPanel } from "./assistant-panel";
import { AssistantToggle } from "./assistant-toggle";
import { AssistantProvider } from "./assistant-provider";
import { ASSISTANT_OFFSET_Y_COOKIE } from "./assistant-panel-state";

function renderToggle(draggable = true) {
  // The offset is persisted in a cookie, which outlives a test's DOM; clear it
  // so each render starts at the default instead of a previous test's value.
  document.cookie = `${ASSISTANT_OFFSET_Y_COOKIE}=; path=/; max-age=0`;
  // `assistant_collapsed: true` keeps the panel closed, so the toggle is the
  // surface under test instead of unmounting itself once the tree loads.
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ assistant_collapsed: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AssistantProvider>
        <AssistantToggle draggable={draggable} />
      </AssistantProvider>
    </QueryClientProvider>,
  );
}

describe("AssistantToggle", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the panel affordance and reports its state when closed", async () => {
    const { getByRole } = await renderToggle();

    const button = getByRole("button", { name: "Ask Nove" });
    await expect.element(button).toBeInTheDocument();
    expect(button.element().getAttribute("aria-pressed")).toBe("false");
    expect(button.element().getAttribute("aria-expanded")).toBe("false");
    expect(button.element().getAttribute("aria-controls")).toBe(
      "assistant-panel",
    );
    expect(button.element().parentElement?.parentElement).toBe(document.body);
  });

  it("keeps the primary fill compact while preserving its width", async () => {
    const { getByRole } = await renderToggle();
    // The browser test runner does not load the Tailwind stylesheet, so the
    // measured box is the UA default. Assert the sizing contract instead.
    const classes = getByRole("button", { name: "Ask Nove" }).element()
      .className;
    expect(classes).toContain("min-h-9");
    expect(classes).toContain("min-w-11");
    expect(classes).toContain("bg-primary");
    expect(classes).toContain("text-primary-foreground");
  });

  it("shows a six-dot grip affordance, hidden until hover", async () => {
    const { getByRole } = await renderToggle();
    const button = getByRole("button", { name: "Ask Nove" }).element();

    const grip = button.querySelector("span[class*='grid-cols-2']");
    expect(grip).not.toBeNull();
    expect(grip?.getAttribute("aria-hidden")).toBe("true");
    expect(grip?.className).toContain("opacity-0");
    expect(grip?.className).toContain("group-hover:opacity-100");
    expect(grip?.querySelectorAll("span").length).toBe(6);
    expect(button.className).toContain("touch-none");
    expect(button.className).toContain("group-hover:min-w-16");
  });

  it("is not draggable without the prop, and carries no grip", async () => {
    const { getByRole, container } = await renderToggle(false);
    const button = getByRole("button", { name: "Ask Nove" }).element();
    await expect
      .element(getByRole("button", { name: "Ask Nove" }))
      .toBeInTheDocument();
    expect(container.querySelector("span[class*='grid-cols-2']")).toBeNull();
    expect(button.className).not.toContain("touch-none");
  });

  it("drags the whole button upward and persists the offset", async () => {
    // Pin the geometry the gesture reads: the button rect and window height.
    const rect = {
      top: 400,
      bottom: 448,
      left: 0,
      right: 48,
      width: 48,
      height: 48,
      x: 0,
      y: 400,
      toJSON: () => ({}),
    } as DOMRect;
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(800);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
      rect,
    );

    const { getByRole } = await renderToggle();
    const button = getByRole("button", { name: "Ask Nove" }).element();
    const anchor = button.closest("div[style]") as HTMLElement;

    // Omit `pointerId` on purpose: React reads it as 0 on both the start event
    // and the window moves, which is what the handler compares.
    button.dispatchEvent(
      new PointerEvent("pointerdown", {
        clientY: 500,
        button: 0,
        bubbles: true,
      }),
    );
    // Dragging up 60px (clientY 500 -> 440) increases the offset by 60.
    window.dispatchEvent(new PointerEvent("pointermove", { clientY: 440 }));
    await vi.waitFor(() => expect(anchor.style.bottom).toContain("60px"));

    window.dispatchEvent(new PointerEvent("pointerup", { clientY: 440 }));
    expect(anchor.style.bottom).toContain("60px");
    expect(document.cookie).toContain(`${ASSISTANT_OFFSET_Y_COOKIE}=60`);

    // A drag is not a click: the toggle is still mounted (the panel did not
    // open, which would have hidden it).
    await expect
      .element(getByRole("button", { name: "Ask Nove" }))
      .toBeInTheDocument();
  });

  it("ignores a press that does not move, so a click still toggles", async () => {
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(800);

    const { getByRole } = await renderToggle();
    const button = getByRole("button", { name: "Ask Nove" }).element();

    button.dispatchEvent(
      new PointerEvent("pointerdown", {
        clientY: 500,
        button: 0,
        bubbles: true,
      }),
    );
    // 2px is below the click threshold and must not start a drag.
    window.dispatchEvent(new PointerEvent("pointermove", { clientY: 498 }));
    window.dispatchEvent(new PointerEvent("pointerup", { clientY: 498 }));

    await button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    // The toggle hides itself once the panel is open, which is the observable
    // effect of the click reaching `toggle`. The query re-evaluates each poll.
    await expect
      .poll(
        () => document.querySelector('button[aria-label="Ask Nove"]') === null,
      )
      .toBe(true);
  });

  it("moves up on ArrowUp and down on ArrowDown, clamped", async () => {
    // Pin the geometry: the clamp reads the button's real rect and the window
    // height, which are not meaningful in the headless runner.
    const rect = {
      top: 400,
      bottom: 448,
      left: 0,
      right: 48,
      width: 48,
      height: 48,
      x: 0,
      y: 400,
      toJSON: () => ({}),
    } as DOMRect;
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(800);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
      rect,
    );

    const { getByRole } = await renderToggle();
    const button = getByRole("button", { name: "Ask Nove" }).element();

    await button.dispatchEvent(
      new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }),
    );
    expect(document.cookie).toContain(`${ASSISTANT_OFFSET_Y_COOKIE}=16`);

    await button.dispatchEvent(
      new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
    );
    expect(document.cookie).toContain(`${ASSISTANT_OFFSET_Y_COOKIE}=0`);
  });

  it("ignores unrelated keys on the button", async () => {
    const { getByRole } = await renderToggle();
    const button = getByRole("button", { name: "Ask Nove" }).element();
    await button.dispatchEvent(
      new KeyboardEvent("keydown", { key: "a", bubbles: true }),
    );
    expect(document.cookie).not.toContain(`${ASSISTANT_OFFSET_Y_COOKIE}=`);
  });
});

describe("AssistantPanel motion guard", () => {
  it("keeps the reduced-motion variant on the animated wrapper", async () => {
    await page.viewport(1440, 900);
    try {
      const { container } = await render(
        <AssistantPanel open onOpenChange={() => {}} />,
      );
      const wrapper =
        container.querySelector("#assistant-panel")?.parentElement;
      expect(wrapper?.className).toContain("transition-[width]");
      expect(wrapper?.className).toContain("motion-reduce:transition-none");
    } finally {
      await page.viewport(375, 800);
    }
  });
});

describe("AssistantPanel narrow ladder", () => {
  it("renders the inline panel from md (768px) up", async () => {
    await page.viewport(768, 900);
    try {
      const { getByRole } = await render(
        <AssistantPanel open onOpenChange={() => {}} />,
      );
      await expect
        .element(getByRole("complementary", { name: "Nove" }))
        .toBeInTheDocument();
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("renders the Sheet just below md (767px)", async () => {
    await page.viewport(767, 900);
    try {
      const { getByRole, container } = await render(
        <AssistantPanel open onOpenChange={() => {}} />,
      );
      await expect.element(getByRole("dialog")).toBeInTheDocument();
      expect(container.querySelector("#assistant-panel")).toBeNull();
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("keeps the panel out of the tab order while closed on a wide viewport", async () => {
    await page.viewport(1440, 900);
    try {
      const { container } = await render(
        <AssistantPanel open={false} onOpenChange={() => {}} />,
      );
      const aside = container.querySelector("#assistant-panel");
      expect(aside).not.toBeNull();
      expect(aside?.hasAttribute("inert")).toBe(true);
    } finally {
      await page.viewport(375, 800);
    }
  });

  it("removes inert and exposes the panel once open on a wide viewport", async () => {
    await page.viewport(1440, 900);
    try {
      const { container } = await render(
        <AssistantPanel open onOpenChange={() => {}} />,
      );
      const aside = container.querySelector("#assistant-panel");
      expect(aside?.hasAttribute("inert")).toBe(false);
    } finally {
      await page.viewport(375, 800);
    }
  });
});
