import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { LayoutProvider } from "@/context/layout-provider";
import { ThemeProvider } from "@/context/theme-provider";
import { getCookie, setCookie } from "@/lib/cookies";
import { clearCookies } from "@/test-utils/cookies";
import { useAuthStore } from "@/stores/auth-store";
import {
  Sidebar,
  SidebarContent,
  SidebarInset,
  SidebarMenuButton,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { AppSidebar } from "./app-sidebar";
import "@/styles/index.css";

function consoleRouter() {
  const root = createRootRoute({
    component: () => (
      <ThemeProvider defaultTheme="light">
        <LayoutProvider>
          <SidebarProvider defaultOpen={getCookie("sidebar_state") !== "false"}>
            <AppSidebar />
            <SidebarInset className="min-w-0 h-svh overflow-hidden md:peer-data-[variant=inset]:h-[calc(100svh-1rem)]">
              <header className="flex h-14 shrink-0 items-center gap-3 border-b px-4">
                <SidebarTrigger />
                Nova workspace
              </header>
              <main className="min-h-0 flex-1 overflow-auto bg-background p-4">
                <h1 className="text-lg font-medium">Workspaces</h1>
                <div className="mt-4 rounded-lg border bg-card p-4">
                  Solid data workspace
                </div>
                {Array.from({ length: 50 }, (_, index) => (
                  <p key={index}>Workspace row {index + 1}</p>
                ))}
              </main>
            </SidebarInset>
          </SidebarProvider>
        </LayoutProvider>
      </ThemeProvider>
    ),
  });
  const paths = ["/", "/workspaces", "/agents", "/settings/profile"] as const;
  const routeTree = root.addChildren(
    paths.map((path) => createRoute({ getParentRoute: () => root, path })),
  );
  return createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ["/workspaces"] }),
  });
}

function contrastRatio(foreground: string, background: string) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d")!;
  const luminance = (color: string) => {
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = color;
    context.fillRect(0, 0, 1, 1);
    const channels = Array.from(context.getImageData(0, 0, 1, 1).data)
      .slice(0, 3)
      .map((channel) => channel / 255);
    return channels
      .map((channel) =>
        channel <= 0.04045
          ? channel / 12.92
          : ((channel + 0.055) / 1.055) ** 2.4,
      )
      .reduce(
        (sum, channel, index) =>
          sum + channel * [0.2126, 0.7152, 0.0722][index],
        0,
      );
  };
  const fg = luminance(foreground);
  const bg = luminance(background);
  return (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
}

function compositeColor(foreground: string, background: string) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d")!;
  context.fillStyle = background;
  context.fillRect(0, 0, 1, 1);
  context.fillStyle = foreground;
  context.fillRect(0, 0, 1, 1);
  const channels = Array.from(context.getImageData(0, 0, 1, 1).data).slice(
    0,
    3,
  );
  return `rgb(${channels.join(" ")})`;
}

function materialShell() {
  return document.querySelector<HTMLElement>(".sidebar-navigation-material")!;
}

function expectFullDesktopPane() {
  const shell = materialShell();
  const inner = shell.querySelector<HTMLElement>(
    '[data-slot="sidebar-inner"]',
  )!;
  const bounds = shell.getBoundingClientRect();
  expect(bounds.top).toBe(0);
  expect(bounds.left).toBe(0);
  expect(bounds.bottom).toBe(window.innerHeight);
  expect(inner.getBoundingClientRect().top).toBeGreaterThanOrEqual(bounds.top);
  const innerStyle = getComputedStyle(inner);
  expect(innerStyle.backgroundColor).toBe("rgba(0, 0, 0, 0)");
  expect(innerStyle.borderTopWidth).toBe("0px");
  expect(innerStyle.borderRadius).toBe("0px");
  expect(innerStyle.boxShadow).toBe("none");
  for (const pseudo of ["::before", "::after"]) {
    const layer = getComputedStyle(shell, pseudo);
    expect(layer.top).toBe("0px");
    expect(layer.right).toBe("0px");
    expect(layer.bottom).toBe("0px");
    expect(layer.left).toBe("0px");
  }
}

function expectSolidContent() {
  const inset = document.querySelector<HTMLElement>(
    '[data-slot="sidebar-inset"]',
  )!;
  const main = inset.querySelector("main")!;
  for (const element of [inset, main, main.querySelector(".bg-card")!]) {
    const style = getComputedStyle(element);
    expect(style.backgroundColor).not.toBe("rgba(0, 0, 0, 0)");
    expect(style.backgroundColor).not.toMatch(/\/\s*0\.|rgba\([^)]*,\s*0\./);
    expect(style.backdropFilter).toBe("none");
    expect(element.closest(".sidebar-navigation-material")).toBeNull();
  }
}

const restoreRules: (() => void)[] = [];

// Exercise the real cascade: turn off only the material capability rule, or
// enable its actual media override. Mocking CSS.supports would not change CSS.
function changeMaterialRule(kind: "supports" | "transparency" | "motion") {
  let changed = false;
  for (const sheet of Array.from(document.styleSheets)) {
    for (let index = 0; index < sheet.cssRules.length; index++) {
      const rule = sheet.cssRules[index];
      if (
        kind === "supports" &&
        rule instanceof CSSSupportsRule &&
        rule.cssText.includes("sidebar-navigation-material")
      ) {
        const original = rule.cssText;
        sheet.deleteRule(index);
        const restoredIndex = index;
        restoreRules.push(() => sheet.insertRule(original, restoredIndex));
        changed = true;
        index--;
        continue;
      }
      if (
        rule instanceof CSSMediaRule &&
        ((kind === "transparency" &&
          rule.conditionText.includes("prefers-reduced-transparency")) ||
          (kind === "motion" &&
            rule.conditionText.includes("prefers-reduced-motion") &&
            rule.cssText.includes("sidebar-navigation-material")))
      ) {
        const original = rule.media.mediaText;
        rule.media.mediaText = "all";
        restoreRules.push(() => {
          rule.media.mediaText = original;
        });
        return;
      }
    }
  }
  if (changed) return;
  throw new Error(`Material ${kind} rule not found`);
}

beforeEach(async () => {
  clearCookies(/^(sidebar_state|layout_|vite-ui-theme)/);
  await page.viewport(1280, 800);
  useAuthStore.getState().auth.setUser({
    username: "navigation-test",
    roles: ["ANALYST"],
    activeRole: "ANALYST",
  });
});

afterEach(async () => {
  restoreRules
    .splice(0)
    .reverse()
    .forEach((restore) => restore());
  clearCookies(/^(sidebar_state|layout_|vite-ui-theme)/);
  document.documentElement.classList.remove("light", "dark");
  useAuthStore.getState().auth.setUser(null);
  await page.viewport(1280, 800);
});

describe("Nova navigation material", () => {
  for (const theme of ["light", "dark"] as const) {
    it(`preserves solid content and neutral navigation through collapse in ${theme}`, async () => {
      setCookie("vite-ui-theme", theme);
      const screen = await render(<RouterProvider router={consoleRouter()} />);
      await expect
        .element(screen.getByRole("link", { name: "Workspaces", exact: true }))
        .toBeVisible();
      const shell = materialShell();
      const tint = getComputedStyle(shell, "::after");
      expect(tint.backdropFilter).toContain("blur(12px)");
      expect(getComputedStyle(shell).backgroundColor).toBe("rgba(0, 0, 0, 0)");
      expectFullDesktopPane();
      expectSolidContent();
      for (const child of Array.from(shell.querySelectorAll("*")))
        expect(getComputedStyle(child).backdropFilter).toBe("none");
      const selected = screen.getByRole("link", {
        name: "Workspaces",
        exact: true,
      });
      const home = screen.getByRole("link", { name: "Home", exact: true });
      const selectedBackground = getComputedStyle(
        selected.element(),
      ).backgroundColor;
      const foreground = getComputedStyle(selected.element()).color;
      const muted = getComputedStyle(shell).getPropertyValue(
        "--sidebar-navigation-muted-foreground",
      );
      const ring = getComputedStyle(shell).getPropertyValue("--sidebar-ring");
      const ambientColors = getComputedStyle(
        shell,
        "::before",
      ).backgroundImage.match(/rgba?\([^)]+\)/g)!;
      for (const background of [
        ...ambientColors.map((color) =>
          compositeColor(tint.backgroundColor, color),
        ),
        selectedBackground,
        getComputedStyle(shell).getPropertyValue("--sidebar-navigation-hover"),
        getComputedStyle(shell).getPropertyValue(
          "--sidebar-navigation-fallback",
        ),
      ]) {
        expect(contrastRatio(foreground, background)).toBeGreaterThanOrEqual(
          4.5,
        );
        expect(contrastRatio(muted, background)).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio(ring, background)).toBeGreaterThanOrEqual(3);
      }
      await page.screenshot({
        path: `../../../coverage/navigation/console-${theme}-expanded.png`,
      });
      home.element().focus();
      await userEvent.keyboard("{Tab}{Shift>}{Tab}{/Shift}");
      await expect.element(home).toHaveFocus();
      expect(getComputedStyle(home.element()).boxShadow).not.toBe("none");
      await userEvent.hover(home);
      expect(getComputedStyle(home.element()).backgroundColor).not.toBe(
        selectedBackground,
      );
      await userEvent.keyboard("{Tab}");
      await screen
        .getByRole("banner")
        .getByRole("button", { name: "Toggle Sidebar", exact: true })
        .click();
      await expect.poll(() => getCookie("sidebar_state")).toBe("false");
      await expect
        .poll(
          () =>
            shell
              .querySelector('[data-slot="sidebar-inner"]')!
              .getBoundingClientRect().width,
        )
        .toBeLessThan(64);
      expectFullDesktopPane();
      expect(getComputedStyle(shell, "::after").backdropFilter).toBe(
        tint.backdropFilter,
      );
      await userEvent.hover(selected);
      await expect
        .element(screen.getByRole("tooltip", { name: "Workspaces" }))
        .toBeVisible();
      await userEvent.unhover(selected);
      await page.screenshot({
        path: `../../../coverage/navigation/console-${theme}-collapsed.png`,
      });
      await screen
        .getByRole("banner")
        .getByRole("button", { name: "Toggle Sidebar", exact: true })
        .click();
      await expect
        .poll(() => shell.getBoundingClientRect().width)
        .toBeGreaterThan(200);
      await screen
        .getByRole("button", { name: "AI & ML", exact: true })
        .click();
      await screen.getByRole("link", { name: "Agent", exact: true }).click();
      await expect
        .element(screen.getByRole("link", { name: "Agent", exact: true }))
        .toHaveAttribute("data-active", "true");
      await page.screenshot({
        path: `../../../coverage/navigation/console-${theme}.png`,
      });
      expectSolidContent();
      const main = screen.getByRole("main").element();
      const header = screen.getByText("Nova workspace").element();
      const headerTop = header.getBoundingClientRect().top;
      main.scrollTop = 200;
      expect(main.scrollTop).toBe(200);
      expect(header.getBoundingClientRect().top).toBe(headerTop);
    });
  }

  it("restores persisted icon mode and keeps collapsed menus solid", async () => {
    setCookie("sidebar_state", "false");
    const screen = await render(<RouterProvider router={consoleRouter()} />);
    await expect
      .element(screen.getByRole("button", { name: "AI & ML", exact: true }))
      .toBeVisible();
    await screen.getByRole("button", { name: "AI & ML", exact: true }).click();
    const menu = screen.getByRole("menu").element();
    expect(getComputedStyle(menu).backdropFilter).toBe("none");
    expect(menu.closest(".sidebar-navigation-material")).toBeNull();
    await screen.getByRole("menuitem", { name: "Agent", exact: true }).click();
    await expect
      .element(screen.getByRole("button", { name: "AI & ML", exact: true }))
      .toHaveAttribute("data-active", "true");
  });

  it("switches appearance through the account menu without changing content material", async () => {
    const screen = await render(<RouterProvider router={consoleRouter()} />);
    await expect
      .element(screen.getByText("navigation-test").first())
      .toBeVisible();
    const before = getComputedStyle(materialShell(), "::after").backgroundColor;
    await screen.getByRole("button", { name: /navigation-test/ }).click();
    await screen.getByRole("menuitem", { name: "Appearance" }).click();
    await page.getByRole("menuitem", { name: /^dark$/i }).click();
    await expect
      .poll(() => document.documentElement.classList.contains("dark"))
      .toBe(true);
    expect(
      getComputedStyle(materialShell(), "::after").backgroundColor,
    ).not.toBe(before);
    expectSolidContent();
  });

  it.each(
    ["light", "dark"].flatMap((theme) =>
      [320, 600].map((width) => ({ theme, width })),
    ),
  )(
    "opens a glass mobile Sheet and closes on navigation in $theme at $width px",
    async ({ theme, width }) => {
      await page.viewport(width, 800);
      setCookie("vite-ui-theme", theme);
      const router = consoleRouter();
      const screen = await render(<RouterProvider router={router} />);
      await screen
        .getByRole("banner")
        .getByRole("button", { name: "Toggle Sidebar", exact: true })
        .click();
      await expect.element(screen.getByRole("dialog")).toBeVisible();
      const shell = materialShell();
      expect(shell.dataset.mobile).toBe("true");
      expect(getComputedStyle(shell, "::before").display).not.toBe("none");
      expect(getComputedStyle(shell, "::before").backdropFilter).toBe("none");
      expect(getComputedStyle(shell).backdropFilter).toContain("blur(12px)");
      expect(getComputedStyle(shell, "::after").display).toBe("none");
      await expect
        .poll(() => Math.round(shell.getBoundingClientRect().left))
        .toBe(0);
      await page.screenshot({
        path: `../../../coverage/navigation/console-mobile-${theme}-${width}.png`,
      });
      screen.getByRole("dialog").element().focus();
      await userEvent.keyboard("{Escape}");
      await expect.element(screen.getByRole("dialog")).not.toBeInTheDocument();
      await screen
        .getByRole("banner")
        .getByRole("button", { name: "Toggle Sidebar", exact: true })
        .click();
      await screen.getByRole("link", { name: "Home", exact: true }).click();
      await expect.poll(() => router.state.location.pathname).toBe("/");
      await expect.element(screen.getByRole("dialog")).not.toBeInTheDocument();
      expectSolidContent();
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
    },
  );

  it.each(["inset", "floating", "sidebar"] as const)(
    "retains %s layout at intermediate width",
    async (variant) => {
      await page.viewport(900, 700);
      setCookie("layout_variant", variant);
      const screen = await render(<RouterProvider router={consoleRouter()} />);
      await expect
        .element(screen.getByRole("link", { name: "Workspaces", exact: true }))
        .toBeVisible();
      const inset = document.querySelector('[data-slot="sidebar-inset"]')!;
      expect(inset.getBoundingClientRect().left).toBeGreaterThanOrEqual(
        materialShell().getBoundingClientRect().right,
      );
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(900);
      expectFullDesktopPane();
      expectSolidContent();
    },
  );

  it.each(["supports", "transparency"] as const)(
    "uses an opaque, unfiltered fallback for %s",
    async (kind) => {
      const screen = await render(<RouterProvider router={consoleRouter()} />);
      await expect
        .element(screen.getByRole("link", { name: "Workspaces", exact: true }))
        .toBeVisible();
      changeMaterialRule(kind);
      const style = getComputedStyle(materialShell(), "::after");
      expect(style.backdropFilter).toBe("none");
      expect(style.backgroundColor).not.toContain("rgba");
      expectSolidContent();
    },
  );

  it.each(["supports", "transparency"] as const)(
    "keeps the mobile fallback opaque for %s",
    async (kind) => {
      await page.viewport(320, 800);
      const screen = await render(<RouterProvider router={consoleRouter()} />);
      await screen
        .getByRole("banner")
        .getByRole("button", { name: "Toggle Sidebar", exact: true })
        .click();
      await expect.element(screen.getByRole("dialog")).toBeVisible();
      changeMaterialRule(kind);
      const shell = materialShell();
      expect(getComputedStyle(shell).backdropFilter).toBe("none");
      expect(getComputedStyle(shell).backgroundColor).not.toContain("rgba");
      expect(getComputedStyle(shell, "::after").display).toBe("none");
      expect(getComputedStyle(shell, "::before").display).toBe("none");
      expectSolidContent();
    },
  );

  it("keeps mobile glass static when motion is reduced", async () => {
    await page.viewport(320, 800);
    changeMaterialRule("motion");
    const screen = await render(<RouterProvider router={consoleRouter()} />);
    await screen
      .getByRole("banner")
      .getByRole("button", { name: "Toggle Sidebar", exact: true })
      .click();
    await expect.element(screen.getByRole("dialog")).toBeVisible();
    const style = getComputedStyle(materialShell());
    expect(style.animationName).toBe("none");
    expect(style.transitionProperty).toBe("none");
    expect(style.backdropFilter).toContain("blur(12px)");
  });

  it("keeps the primitive solid by default and supports a non-collapsible material shell", async () => {
    const screen = await render(
      <SidebarProvider>
        <Sidebar collapsible="none">
          <SidebarContent>Solid navigation</SidebarContent>
        </Sidebar>
        <Sidebar material="navigation" collapsible="none">
          <SidebarMenuButton>Navigation material</SidebarMenuButton>
        </Sidebar>
      </SidebarProvider>,
    );
    expect(
      screen
        .getByText("Solid navigation")
        .element()
        .closest(".sidebar-navigation-material"),
    ).toBeNull();
    expect(
      document.querySelectorAll(".sidebar-navigation-material"),
    ).toHaveLength(1);
    expect(
      getComputedStyle(materialShell(), "::after").backdropFilter,
    ).toContain("blur(12px)");
  });
});
