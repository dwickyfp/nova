import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api-client";
import { Main } from "@/components/layout/main";
import { ThemeProvider } from "@/context/theme-provider";
import { MonitoringPageScroller } from "../index";
import { MonitoringAI } from "./index";
import {
  fetchAIUsage,
  reportedTokens,
  usageInsights,
  type AIUsage,
  type UsageStats,
} from "./api";
import "@/styles/index.css";

vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  fetchAIUsage: vi.fn(),
}));

const stats: UsageStats = {
  activities: 30,
  reported_activities: 25,
  unreported_activities: 5,
  input_tokens: 8000,
  output_tokens: 2000,
  total_tokens: 10000,
  unattributed_tokens: 1500,
  users: 2,
  models: 1,
  failed_activities: 1,
  avg_tokens: 400,
  coverage_percent: 83.3,
  avg_duration_ms: null,
};

const fixture: AIUsage = {
  days: 7,
  start: "2026-09-19T00:00:00Z",
  end: "2026-09-25T12:00:00Z",
  previous_start: "2026-09-12T00:00:00Z",
  previous_end: "2026-09-18T12:00:00Z",
  summary: stats,
  previous_summary: { ...stats, total_tokens: 5000 },
  token_change_percent: 100,
  daily: Array.from({ length: 7 }, (_, index) => ({
    ...stats,
    date: `2026-09-${19 + index}`,
    total_tokens: index === 6 ? 10000 : 0,
  })),
  actions: [
    { ...stats, name: "Nova Studio / Assistant" },
    {
      ...stats,
      name: "AI_COMPLETE",
      activities: 5,
      reported_activities: 0,
      total_tokens: 0,
      avg_tokens: null,
    },
  ],
  models: [
    { ...stats, name: "test-model", total_tokens: 8500 },
    { ...stats, name: null, total_tokens: 1500 },
  ],
  users: [
    { ...stats, name: "analyst-a" },
    { ...stats, name: "analyst-b", total_tokens: 2000 },
  ],
  sources: [{ ...stats, name: "assistant" }],
  coverage: [
    {
      source: "assistant",
      period: "current",
      status: "available",
      activities: 25,
    },
    {
      source: "functions",
      period: "current",
      status: "available",
      activities: 5,
    },
  ],
  partial: false,
  source_limit: 10000,
  filters: { models: ["test-model"], users: ["analyst-a", "analyst-b"] },
  activities: Array.from({ length: 25 }, (_, index) => ({
    id: `test-${index}`,
    at: "2026-09-25T10:00:00Z",
    user_name: "analyst-a",
    source: "assistant",
    action: "Nova Studio / Assistant",
    model: "test-model",
    input_tokens: 100,
    output_tokens: 20,
    total_tokens: 120,
    status: "recorded",
    duration_ms: null,
  })),
  total: 30,
  offset: 0,
  limit: 25,
};

let client: QueryClient;
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  vi.mocked(fetchAIUsage).mockImplementation(async (filters) => ({
    ...fixture,
    days: filters.days,
    offset: filters.offset,
    activities: filters.offset
      ? fixture.activities.slice(0, 5)
      : fixture.activities,
  }));
});
afterEach(async () => {
  client.clear();
  vi.resetAllMocks();
  document.documentElement.classList.remove("dark");
  await page.viewport(1280, 800);
});

async function mount({
  dark = false,
  assistant = false,
}: { dark?: boolean; assistant?: boolean } = {}) {
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider
        defaultTheme={dark ? "dark" : "light"}
        storageKey="ai-monitoring-test-theme"
      >
        <div
          style={{ height: "100dvh" }}
          className="flex min-h-0 min-w-0 bg-background text-foreground"
        >
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <header className="shrink-0 border-b p-3">
              Nova console test header
            </header>
            <Main fixed fluid className="min-h-0 p-0">
              <MonitoringPageScroller>
                <MonitoringAI />
              </MonitoringPageScroller>
            </Main>
          </div>
          {assistant && (
            <aside style={{ width: "35%" }} className="shrink-0 border-l p-3">
              Assistant panel
            </aside>
          )}
        </div>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("AI Monitoring", () => {
  it("supports keyboard selection and closing the period menu", async () => {
    const view = await mount();
    await expect
      .element(view.getByText("83.3%", { exact: true }))
      .toBeVisible();
    const trigger = view.container.querySelector(
      '[aria-label="Period"]',
    ) as HTMLElement;
    trigger.focus();
    await userEvent.keyboard("{Enter}");
    await expect
      .element(page.getByRole("option", { name: "Last 14 days" }))
      .toBeVisible();
    await userEvent.keyboard("{Escape}");
    await expect
      .element(page.getByRole("option", { name: "Last 14 days" }))
      .not.toBeInTheDocument();
    await expect.poll(() => document.activeElement === trigger).toBe(true);
    await userEvent.keyboard("{Tab}");
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Source");
    const focused = getComputedStyle(document.activeElement!);
    expect(
      focused.boxShadow !== "none" || focused.outlineStyle !== "none",
    ).toBe(true);
  });

  it.each([false, true])(
    "keeps text and chart contrast in theme dark=%s",
    async (dark) => {
      const view = await mount({ dark });
      await expect
        .element(view.getByText("83.3%", { exact: true }))
        .toBeVisible();
      const context = document.createElement("canvas").getContext("2d")!;
      const tokens = getComputedStyle(document.documentElement);
      const luminance = (token: string) => {
        context.fillStyle = tokens.getPropertyValue(token).trim();
        context.fillRect(0, 0, 1, 1);
        const rgb = [...context.getImageData(0, 0, 1, 1).data]
          .slice(0, 3)
          .map((channel) => {
            const value = channel / 255;
            return value <= 0.04045
              ? value / 12.92
              : ((value + 0.055) / 1.055) ** 2.4;
          });
        return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
      };
      const ratio = (a: string, b: string) => {
        const [bright, dim] = [luminance(a), luminance(b)].sort(
          (x, y) => y - x,
        );
        return (bright + 0.05) / (dim + 0.05);
      };
      for (const surface of ["--background", "--card", "--surface-2"]) {
        expect(ratio("--foreground", surface)).toBeGreaterThanOrEqual(4.5);
        expect(ratio("--muted-foreground", surface)).toBeGreaterThanOrEqual(
          4.5,
        );
      }
      expect(ratio("--chart-1", "--card")).toBeGreaterThanOrEqual(3);
    },
  );
  it("shows reported usage and explains missing usage without guessing tokens", async () => {
    const view = await mount();
    await expect
      .element(view.getByRole("heading", { name: "AI Monitoring" }))
      .toBeVisible();
    await expect
      .element(view.getByText("83.3%", { exact: true }))
      .toBeVisible();
    await expect
      .element(view.getByText("+100% vs previous 7 days"))
      .toBeVisible();
    await expect
      .element(
        view.getByText("5 activities have no token total.", { exact: false }),
      )
      .toBeVisible();
    const table = view.getByRole("table", { name: "Token usage by action" });
    await expect
      .element(
        table
          .getByRole("row")
          .filter({ hasText: "AI_COMPLETE" })
          .getByText("Unavailable")
          .first(),
      )
      .toBeInTheDocument();
    expect(usageInsights(fixture).join(" ")).toContain(
      "1,500 tokens have no recorded model attribution",
    );
    expect(reportedTokens({ ...stats, reported_activities: 0 })).toBe(
      "Unavailable",
    );
    expect(
      reportedTokens({
        ...stats,
        activities: 0,
        reported_activities: 0,
        total_tokens: 0,
      }),
    ).toBe("0");
  });

  it("filters the full dashboard, resets pagination, and supports refresh controls", async () => {
    const view = await mount();
    await view.getByRole("button", { name: "Next", exact: true }).click();
    await expect
      .poll(() => vi.mocked(fetchAIUsage).mock.lastCall?.[0].offset)
      .toBe(25);
    await view.getByRole("button", { name: "Previous", exact: true }).click();
    await expect
      .poll(() => vi.mocked(fetchAIUsage).mock.lastCall?.[0].offset)
      .toBe(0);
    await view.getByRole("combobox", { name: "Period" }).click();
    await page.getByRole("option", { name: "Last 14 days" }).click();
    await expect
      .poll(() => vi.mocked(fetchAIUsage).mock.lastCall?.[0].days)
      .toBe(14);
    await view.getByRole("combobox", { name: "Source" }).click();
    await page.getByRole("option", { name: "AI Functions" }).click();
    await expect
      .poll(() => vi.mocked(fetchAIUsage).mock.lastCall?.[0].source)
      .toBe("functions");
    await view.getByRole("combobox", { name: "Model" }).click();
    await page.getByRole("option", { name: "test-model" }).click();
    await expect
      .poll(() => vi.mocked(fetchAIUsage).mock.lastCall?.[0].model)
      .toBe("test-model");
    await view.getByRole("combobox", { name: "User" }).click();
    await page.getByRole("option", { name: "analyst-b" }).click();
    await expect
      .poll(() => vi.mocked(fetchAIUsage).mock.lastCall?.[0].user_name)
      .toBe("analyst-b");
    await view.getByRole("button", { name: "Clear filters" }).click();
    await expect
      .element(view.getByRole("combobox", { name: "Source" }))
      .toHaveTextContent("All sources");
    await expect
      .element(view.getByRole("combobox", { name: "Period" }))
      .toHaveTextContent("Last 14 days");
    await view.getByRole("button", { name: "Auto-refresh: on (60s)" }).click();
    await expect
      .element(view.getByRole("button", { name: "Auto-refresh: off" }))
      .toHaveAttribute("aria-pressed", "false");
    const count = vi.mocked(fetchAIUsage).mock.calls.length;
    await view.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect
      .poll(() => vi.mocked(fetchAIUsage).mock.calls.length)
      .toBe(count + 1);
  });

  it("lets the user inspect rankings and exact daily values", async () => {
    const view = await mount();
    await view
      .getByRole("button", { name: "Filter by model test-model" })
      .click();
    await expect
      .poll(() => vi.mocked(fetchAIUsage).mock.lastCall?.[0].model)
      .toBe("test-model");
    await view
      .getByRole("button", { name: "Filter by user analyst-a" })
      .click();
    await expect
      .poll(() => vi.mocked(fetchAIUsage).mock.lastCall?.[0].user_name)
      .toBe("analyst-a");
    await view.getByText("Daily breakdown", { exact: true }).click();
    await expect
      .element(view.getByRole("table", { name: "Daily token breakdown" }))
      .toBeVisible();
    await view.getByText("Source availability", { exact: true }).click();
    await expect
      .element(
        view.getByRole("table", { name: "AI usage source availability" }),
      )
      .toBeVisible();
  });

  it("shows loading, error, retry, and administrator access states", async () => {
    vi.mocked(fetchAIUsage).mockReturnValueOnce(new Promise(() => {}));
    const loading = await mount();
    await expect
      .element(loading.getByRole("status"))
      .toHaveTextContent("Loading AI usage history");
    await loading.unmount();
    client.clear();
    vi.mocked(fetchAIUsage).mockRejectedValueOnce(new Error("offline"));
    const failed = await mount();
    await expect
      .element(failed.getByText("Could not load AI usage"))
      .toBeVisible();
    await failed.getByRole("button", { name: "Retry" }).click();
    await expect
      .element(failed.getByRole("heading", { name: "Daily token usage" }))
      .toBeVisible();
    await failed.unmount();
    client.clear();
    vi.mocked(fetchAIUsage).mockRejectedValueOnce(
      new ApiError(403, "forbidden"),
    );
    const denied = await mount();
    await expect
      .element(denied.getByText("Administrator access required"))
      .toBeVisible();
    await expect
      .element(denied.getByRole("heading", { name: "Daily token usage" }))
      .not.toBeInTheDocument();
  });

  it("marks partial results and distinguishes empty activity from missing token metadata", async () => {
    vi.mocked(fetchAIUsage).mockResolvedValueOnce({
      ...fixture,
      partial: true,
      token_change_percent: null,
      previous_summary: null,
    });
    const partial = await mount();
    await expect.element(partial.getByText("Incomplete history")).toBeVisible();
    await expect
      .element(partial.getByText("No comparable previous token total"))
      .toBeVisible();
    await partial.unmount();
    client.clear();
    vi.mocked(fetchAIUsage).mockResolvedValueOnce({
      ...fixture,
      total: 0,
      activities: [],
      actions: [],
      models: [],
      users: [],
      summary: {
        ...stats,
        activities: 0,
        reported_activities: 0,
        total_tokens: 0,
        coverage_percent: null,
      },
    });
    const empty = await mount();
    await expect
      .element(empty.getByText("No AI activity in this selection"))
      .toBeVisible();
    await expect
      .element(empty.getByText("No activity", { exact: true }))
      .toBeVisible();
  });

  it.each([
    { width: 320, dark: false, assistant: false },
    { width: 1280, dark: false, assistant: true },
    { width: 1280, dark: true, assistant: false },
  ])(
    "contains long content at $width px (dark=$dark, assistant=$assistant)",
    async ({ width, dark, assistant }) => {
      await page.viewport(width, 800);
      const view = await mount({ dark, assistant });
      await expect
        .element(view.getByText("83.3%", { exact: true }))
        .toBeVisible();
      const scroller = view.container.querySelector(
        ".overflow-y-auto",
      ) as HTMLElement;
      const header = view.container.querySelector("header")!;
      const before = header.getBoundingClientRect().top;
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
      expect(scroller.scrollHeight).toBeGreaterThan(scroller.clientHeight);
      scroller.scrollTop = scroller.scrollHeight;
      expect(header.getBoundingClientRect().top).toBe(before);
      expect(document.documentElement.scrollHeight).toBeLessThanOrEqual(820);
      scroller.scrollTop = 0;
      await page.screenshot({
        path: `__screenshots__/ai-monitoring-${width}-${dark}-${assistant}.png`,
      });
    },
  );
});
