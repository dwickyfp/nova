import { beforeEach, describe, expect, it } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { AppTitle } from "@/components/layout/app-title";
import {
  Sidebar,
  SidebarHeader,
  SidebarProvider,
} from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { StudioSidebar } from "@/features/studio/studio-sidebar";
import { Wordmark } from "./wordmark";
import "@/styles/index.css";

function consoleSidebar(open: boolean) {
  const root = createRootRoute({
    component: () => (
      <SidebarProvider defaultOpen={open}>
        <Sidebar collapsible="icon">
          <SidebarHeader>
            <AppTitle />
          </SidebarHeader>
        </Sidebar>
      </SidebarProvider>
    ),
  });
  return createRouter({
    routeTree: root,
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
}

function studioSidebar(open: boolean) {
  return (
    <TooltipProvider>
      <StudioSidebar
        view="chat"
        onView={() => {}}
        threads={[]}
        activeThreadId={null}
        onOpenThread={() => {}}
        onNewChat={() => {}}
        open={open}
        onToggle={() => {}}
      />
    </TooltipProvider>
  );
}

describe("Wordmark", () => {
  it("is one labelled image with the product name", async () => {
    const screen = await render(<Wordmark label="Studio" />);
    const images = screen.container.querySelectorAll("svg");
    expect(images).toHaveLength(1);
    await expect
      .element(screen.getByRole("img", { name: "Nova Studio" }))
      .toBeVisible();
    // The visible label is decoration for the image's accessible name.
    expect(screen.getByText("Studio").element()).toHaveAttribute(
      "aria-hidden",
      "true",
    );
  });

  it("names itself Nova without a label", async () => {
    const screen = await render(<Wordmark />);
    await expect
      .element(screen.getByRole("img", { name: "Nova" }))
      .toBeVisible();
  });

  it("scales from the size prop and keeps the 76:28 proportion", async () => {
    const screen = await render(<Wordmark size={28} />);
    const svg = screen.getByRole("img", { name: "Nova" }).element();
    const box = svg.getBoundingClientRect();
    expect(box.height).toBe(28);
    expect(box.width).toBe(76);
  });

  it("draws the strokes in the text colour and the spark in --brand-mark", async () => {
    const screen = await render(
      <div style={{ color: "rgb(1, 2, 3)" }}>
        <Wordmark />
      </div>,
    );
    const svg = screen.getByRole("img", { name: "Nova" }).element();
    const strokes = svg.querySelector("g") as SVGGElement;
    expect(getComputedStyle(strokes).stroke).toBe("rgb(1, 2, 3)");
    const spark = svg.querySelector(
      "[data-slot=wordmark-spark]",
    ) as SVGPathElement;
    const brandMark = getComputedStyle(document.documentElement)
      .getPropertyValue("--brand-mark")
      .trim();
    expect(brandMark).not.toBe("");
    const probe = document.createElement("span");
    probe.style.color = brandMark;
    document.body.append(probe);
    expect(getComputedStyle(spark).fill).toBe(getComputedStyle(probe).color);
    probe.remove();
  });
});

describe("Wordmark in the sidebars", () => {
  beforeEach(async () => {
    await page.viewport(1280, 800);
  });

  it("shows the Console wordmark next to the phoenix when expanded", async () => {
    const screen = await render(<RouterProvider router={consoleSidebar(true)} />);
    await expect
      .element(
        screen.getByRole("img", { name: "Nova Enterprise Intelligence OS" }),
      )
      .toBeVisible();
    expect(screen.container.querySelector("img")).not.toBeNull();
  });

  it("shows only the phoenix in the collapsed Console sidebar", async () => {
    const screen = await render(<RouterProvider router={consoleSidebar(false)} />);
    const link = screen.getByRole("link").first();
    await expect.element(link).toBeVisible();
    const wordmark = screen.container.querySelector(
      "[data-slot=wordmark]",
    ) as HTMLElement;
    expect(wordmark).not.toBeNull();
    expect(wordmark.checkVisibility()).toBe(false);
    const phoenix = Array.from(
      screen.container.querySelectorAll("img"),
    ).find((img) => img.checkVisibility());
    expect(phoenix).toBeDefined();
  });

  it("shows nova STUDIO in the expanded Studio sidebar", async () => {
    const screen = await render(studioSidebar(true));
    await expect
      .element(screen.getByRole("img", { name: "Nova Studio" }))
      .toBeVisible();
  });

  it("shows only the phoenix in the collapsed Studio sidebar", async () => {
    const screen = await render(studioSidebar(false));
    await expect
      .element(screen.getByRole("button", { name: "Expand sidebar" }))
      .toBeVisible();
    expect(
      screen.container.querySelector("[data-slot=wordmark]"),
    ).toBeNull();
  });
});
