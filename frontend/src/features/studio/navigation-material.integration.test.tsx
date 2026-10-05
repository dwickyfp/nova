import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { ThemeProvider } from "@/context/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  agentsApi,
  studioApi,
  type Agent,
  type AgentThread,
} from "@/features/agents/api";
import { clearCookies } from "@/test-utils/cookies";
import { setCookie } from "@/lib/cookies";
import { StudioApp } from "./index";
import "@/styles/index.css";

// Keep the real app shell, sidebar, history, account menu, and theme provider.
// Heavy workspace features are fixtures; these checks do not prove API access.
vi.mock("./studio-chat", () => ({
  StudioChat: () => (
    <section className="flex min-h-0 flex-1 flex-col">
      <header className="h-14 shrink-0 border-b p-4">Studio transcript</header>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        Solid transcript workspace
      </div>
    </section>
  ),
}));
vi.mock("./studio-artifacts", () => ({
  StudioArtifacts: () => <section>Artifacts workspace</section>,
}));
vi.mock("./studio-intelligence", () => ({
  StudioIntelligence: () => <section>News workspace</section>,
}));
vi.mock("./news/newspaper-page", () => ({
  NewspaperPage: () => <section>News workspace</section>,
}));
vi.mock("./studio-capabilities", () => ({
  StudioCapabilities: () => <section>Capabilities workspace</section>,
}));
vi.mock("./studio-dashboards", () => ({
  StudioDashboards: () => <section>Dashboard workspace</section>,
}));
vi.mock("./studio-shared", () => ({
  StudioShared: () => <section>Shared workspace</section>,
}));

const clients: QueryClient[] = [];
const views: Awaited<ReturnType<typeof render>>[] = [];

beforeEach(async () => {
  clearCookies(/^(sidebar_state|vite-ui-theme)/);
  vi.spyOn(agentsApi, "listStudio").mockResolvedValue({
    count: 1,
    agents: [
      { agent_id: "navigation-agent", name: "Navigation specialist" } as Agent,
    ],
  });
  vi.spyOn(agentsApi, "listThreads").mockResolvedValue({
    count: 32,
    threads: Array.from(
      { length: 32 },
      (_, index) =>
        ({
          agent_id: "navigation-agent",
          thread_id: index === 0 ? "navigation-thread" : `thread-${index}`,
          title:
            index === 0
              ? "Revenue by category over the last twelve months"
              : `History conversation ${index}`,
          updated_at: new Date().toISOString(),
          created_at: new Date().toISOString(),
          message_count: 2,
        }) as AgentThread,
    ),
  });
  vi.spyOn(studioApi, "settings").mockResolvedValue({
    identity: {
      username: "studio-navigation-test",
      active_role: "ANALYST",
      roles: ["ANALYST"],
      // News is off for an account until an administrator enables it.
      news_enabled: true,
    },
    preferences: {},
  } as never);
});

afterEach(async () => {
  for (const view of views.splice(0)) await view.unmount();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
  clearCookies(/^(sidebar_state|vite-ui-theme)/);
  document.documentElement.classList.remove("light", "dark");
  await page.viewport(1280, 800);
});

async function renderStudio() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const root = createRootRoute({
    component: () => (
      <QueryClientProvider client={client}>
        <ThemeProvider defaultTheme="light">
          <TooltipProvider>
            <StudioApp />
          </TooltipProvider>
        </ThemeProvider>
      </QueryClientProvider>
    ),
  });
  const studio = createRoute({
    getParentRoute: () => root,
    path: "/studio",
    validateSearch: (search: Record<string, unknown>) => search,
  });
  const router = createRouter({
    routeTree: root.addChildren([studio]),
    history: createMemoryHistory({
      initialEntries: [
        "/studio?agent=navigation-agent&thread=navigation-thread",
      ],
    }),
  });
  const screen = await render(<RouterProvider router={router} />);
  views.push(screen);
  return { screen, router };
}

function expectMaterialBoundary() {
  const sidebar = document.querySelector<HTMLElement>("aside")!;
  const main = document.querySelector("main")!;
  expect(sidebar.classList.contains("sidebar-navigation-material")).toBe(true);
  const shellFilter = getComputedStyle(sidebar).backdropFilter;
  const layerFilter = getComputedStyle(sidebar, "::after").backdropFilter;
  expect(shellFilter === "none" ? layerFilter : shellFilter).toContain(
    "blur(12px)",
  );
  if (shellFilter !== "none") expect(layerFilter).toBe("none");
  expect(getComputedStyle(main).backgroundColor).not.toBe("rgba(0, 0, 0, 0)");
  expect(getComputedStyle(main).backdropFilter).toBe("none");
  expect(main.closest(".sidebar-navigation-material")).toBeNull();
  for (const child of Array.from(sidebar.querySelectorAll("*")))
    expect(getComputedStyle(child).backdropFilter).toBe("none");
}

describe("Studio navigation material integration", () => {
  for (const theme of ["light", "dark"] as const) {
    it(`shares navigation material across expanded and collapsed desktop in ${theme}`, async () => {
      await page.viewport(1280, 800);
      setCookie("vite-ui-theme", theme);
      const { screen, router } = await renderStudio();
      await expect
        .element(
          screen.getByText("Revenue by category over the last twelve months", {
            exact: true,
          }),
        )
        .toBeVisible();
      expectMaterialBoundary();
      const selected = screen
        .getByRole("button", { name: /^Revenue by category/ })
        .element()
        .closest("li")!;
      expect(selected.getAttribute("data-active")).toBe("true");
      expect(getComputedStyle(selected).backgroundColor).not.toBe(
        "rgba(0, 0, 0, 0)",
      );
      const sidebar = document.querySelector("aside")!;
      const viewport = sidebar.querySelector<HTMLElement>(
        '[data-slot="scroll-area-viewport"]',
      )!;
      expect(viewport.scrollHeight).toBeGreaterThan(viewport.clientHeight);
      viewport.scrollTop = 200;
      expect(viewport.scrollTop).toBe(200);
      viewport.scrollTop = 0;
      await page.screenshot({
        path: `../../../coverage/navigation/studio-${theme}.png`,
      });
      await screen
        .getByRole("button", { name: "Collapse sidebar", exact: true })
        .click();
      await expect.poll(() => sidebar.getBoundingClientRect().width).toBe(56);
      expectMaterialBoundary();
      const news = screen.getByRole("button", { name: "News", exact: true });
      await userEvent.hover(news);
      await expect
        .element(screen.getByRole("tooltip", { name: "News" }))
        .toBeVisible();
      await userEvent.unhover(news);
      await page.screenshot({
        path: `../../../coverage/navigation/studio-${theme}-collapsed.png`,
      });
      await screen
        .getByRole("button", { name: "Expand sidebar", exact: true })
        .click();
      await screen.getByRole("button", { name: "News", exact: true }).click();
      await expect
        .poll(() => router.state.location.search)
        .toMatchObject({ view: "news" });
      await expect
        .element(screen.getByRole("button", { name: "News", exact: true }))
        .toHaveAttribute("aria-current", "page");
      expectMaterialBoundary();
    });
  }

  it.each(
    ["light", "dark"].flatMap((theme) =>
      [320, 600].map((width) => ({ theme, width })),
    ),
  )(
    "keeps the mobile drawer readable and closes on history, Escape, and scrim in $theme at $width px",
    async ({ theme, width }) => {
      await page.viewport(width, 800);
      setCookie("vite-ui-theme", theme);
      const { screen, router } = await renderStudio();
      await screen
        .getByRole("button", { name: "Expand sidebar", exact: true })
        .click();
      await expect
        .element(
          screen.getByRole("button", { name: "Close navigation", exact: true }),
        )
        .toBeVisible();
      expectMaterialBoundary();
      const shell = document.querySelector("aside")!;
      expect(getComputedStyle(shell.parentElement!).backgroundColor).toBe(
        "rgba(0, 0, 0, 0)",
      );
      expect(getComputedStyle(shell, "::before").display).not.toBe("none");
      expect(getComputedStyle(shell, "::before").backdropFilter).toBe("none");
      await expect
        .element(
          screen.getByText("Revenue by category over the last twelve months", {
            exact: true,
          }),
        )
        .toBeVisible();
      await page.screenshot({
        path: `../../../coverage/navigation/studio-mobile-${theme}-${width}.png`,
      });
      await screen
        .getByRole("button", { name: /^Revenue by category/ })
        .click();
      await expect
        .poll(() => router.state.location.search)
        .toMatchObject({
          agent: "navigation-agent",
          thread: "navigation-thread",
        });
      await expect
        .element(
          screen.getByRole("button", { name: "Close navigation", exact: true }),
        )
        .not.toBeInTheDocument();
      await screen
        .getByRole("button", { name: "Expand sidebar", exact: true })
        .click();
      screen
        .getByRole("button", { name: "Collapse sidebar", exact: true })
        .element()
        .focus();
      await userEvent.keyboard("{Escape}");
      await expect
        .element(
          screen.getByRole("button", { name: "Close navigation", exact: true }),
        )
        .not.toBeInTheDocument();
      await screen
        .getByRole("button", { name: "Expand sidebar", exact: true })
        .click();
      await screen
        .getByRole("button", { name: "Close navigation", exact: true })
        .click({ position: { x: width - 1, y: 10 } });
      await expect
        .element(
          screen.getByRole("button", { name: "Close navigation", exact: true }),
        )
        .not.toBeInTheDocument();
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
    },
  );
});
