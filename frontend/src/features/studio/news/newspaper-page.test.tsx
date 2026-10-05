import { afterEach, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import {
  newspaperApi,
  type Newspaper,
  type Story,
  type StoryDetail,
} from "./newspaper-api";
import { NewspaperPage } from "./newspaper-page";
import "@/styles/index.css";

const series = Array.from({ length: 29 }, (_, index) => ({
  date: new Date(Date.UTC(2026, 8, 6 + index)).toISOString().slice(0, 10),
  value: index === 28 ? 254505601 : 380000000 + (index % 7) * 6000000,
  count: 900,
}));

function story(overrides: Partial<Story> = {}): Story {
  return {
    id: "story-bandung",
    revision: 1,
    view_id: "retail",
    semantic_version: 3,
    metric: "revenue",
    metric_label: "Revenue",
    unit: "IDR",
    slice: { dimension: "city", value: "Bandung" },
    edition_date: "2026-10-04",
    baseline_dates: ["2026-09-27", "2026-09-20", "2026-09-13", "2026-09-06"],
    before: 389127213,
    after: 254505601,
    change: -134621612,
    relative_change: -0.346,
    severity: "critical",
    rank: 1,
    confidence: "medium",
    series,
    drivers: [],
    narrative: {
      headline: "Revenue fell 34.6% in Bandung",
      deck: "IDR 254,505,601 on Sunday, 4 October 2026, against a typical Sunday of IDR 389,127,213.",
      what_happened: "Revenue for city Bandung came to IDR 254,505,601 on Sunday.",
      why_it_matters: "The change is larger than the materiality threshold set for this view.",
      what_to_check: "The cause has not been established. Confirm the data is complete.",
    },
    business_rules: [
      {
        source: "metric",
        name: "Revenue",
        text: "A daily move of more than ten percent must be explained in the weekly review.",
      },
    ],
    narrative_source: "model",
    ...overrides,
  };
}

const online = story({
  id: "story-online",
  slice: { dimension: "channel", value: "Online" },
  severity: "warning",
  rank: 2,
  relative_change: 0.143,
  change: 40000000,
  narrative: { ...story().narrative, headline: "Revenue rose 14.3% in Online" },
});
const paper: Newspaper = {
  edition_date: "2026-10-04",
  sections: [
    {
      view_id: "retail",
      name: "news_retail_sales",
      edition_date: "2026-10-04",
      pressed_at: "2026-10-05T02:00:00Z",
      stories: [online, story()],
    },
  ],
};

function mount(props: Partial<Parameters<typeof NewspaperPage>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <div className="flex h-dvh min-w-0 flex-col">
        <NewspaperPage onOpen={vi.fn()} onAlerts={vi.fn()} onFollowUp={vi.fn()} {...props} />
      </div>
    </QueryClientProvider>,
  );
}

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
  useAuthStore.getState().auth.setUser(null);
  document.documentElement.classList.remove("dark");
});

it.each([
  { width: 320, dark: true },
  { width: 1280, dark: false },
])("lays out the edition at $width px without page overflow", async ({ width, dark }) => {
  await page.viewport(width, 800);
  document.documentElement.classList.toggle("dark", dark);
  vi.spyOn(newspaperApi, "read").mockResolvedValue(paper);
  const onOpen = vi.fn();
  const screen = await mount({ onOpen });

  await expect.element(screen.getByRole("heading", { level: 1, name: "News" })).toBeVisible();
  await expect.element(screen.getByText(/Sunday, 4 October 2026 · 1 view covered/)).toBeVisible();
  // The critical story leads even though the edition lists it second.
  await expect
    .element(screen.getByRole("heading", { level: 2, name: "Revenue fell 34.6% in Bandung" }))
    .toBeVisible();
  await expect.element(screen.getByText("−34.6%").first()).toBeVisible();
  await expect
    .element(screen.getByRole("heading", { level: 3, name: "Revenue rose 14.3% in Online" }))
    .toBeVisible();
  await screen.getByRole("button", { name: "Read the full story" }).click();
  expect(onOpen).toHaveBeenCalledWith("story-bandung");
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
  for (const element of document.querySelectorAll("article, aside, figure"))
    expect(element.getBoundingClientRect().right).toBeLessThanOrEqual(width);
  await page.screenshot();
});

it("explains an edition with nothing the reader can see", async () => {
  vi.spyOn(newspaperApi, "read").mockResolvedValue({
    ...paper,
    sections: [{ ...paper.sections[0], stories: [] }],
  });
  const screen = await mount();

  await expect.element(screen.getByText("Nothing material in this edition")).toBeVisible();
});

it("explains when no edition exists yet", async () => {
  vi.spyOn(newspaperApi, "read").mockResolvedValue({ edition_date: null, sections: [] });
  const screen = await mount();

  await expect.element(screen.getByText("No edition to read yet")).toBeVisible();
});

it("tells a user without the entitlement who can enable News", async () => {
  vi.spyOn(newspaperApi, "read").mockRejectedValue(new ApiError(403, "Denied"));
  const screen = await mount();

  await expect
    .element(screen.getByText("News is not enabled for your account"))
    .toBeVisible();
  expect(screen.getByRole("button", { name: "Retry" }).query()).toBeNull();
});

it("offers a retry when the edition fails to load", async () => {
  const read = vi.spyOn(newspaperApi, "read").mockRejectedValue(new ApiError(500, "Down"));
  const screen = await mount();
  await expect.element(screen.getByText("The edition could not be loaded")).toBeVisible();

  read.mockResolvedValue(paper);
  await screen.getByRole("button", { name: "Retry" }).click();

  await expect
    .element(screen.getByRole("heading", { level: 2, name: "Revenue fell 34.6% in Bandung" }))
    .toBeVisible();
});

it("drops the cached edition when the signed-in principal changes", async () => {
  useAuthStore.getState().auth.setUser({ username: "bandung", roles: ["news_reader"] });
  const read = vi.spyOn(newspaperApi, "read").mockResolvedValue(paper);
  const screen = await mount();
  await expect
    .element(screen.getByRole("heading", { level: 2, name: "Revenue fell 34.6% in Bandung" }))
    .toBeVisible();

  read.mockResolvedValue({ ...paper, sections: [{ ...paper.sections[0], stories: [] }] });
  useAuthStore.getState().auth.setUser({ username: "jakarta", roles: ["news_reader"] });

  await expect.element(screen.getByText("Nothing material in this edition")).toBeVisible();
  expect(screen.getByText("Revenue fell 34.6% in Bandung").query()).toBeNull();
});

it("opens the monitor alerts desk", async () => {
  vi.spyOn(newspaperApi, "read").mockResolvedValue(paper);
  const onAlerts = vi.fn();
  const screen = await mount({ onAlerts });

  await screen.getByRole("button", { name: "Monitor alerts" }).click();

  expect(onAlerts).toHaveBeenCalled();
});

const detail: StoryDetail = {
  ...story({
    slice: null,
    drivers: [
      { dimension: "city", value: "Jakarta", before: 900, after: 1040, change: 140 },
      { dimension: "city", value: "Bandung", before: 400, after: 460, change: 60 },
    ],
  }),
  view_name: "news_retail_sales",
  pressed_at: "2026-10-05T02:00:00Z",
};

it.each([
  { width: 320, dark: false },
  { width: 1280, dark: true },
])("tells the full story at $width px", async ({ width, dark }) => {
  await page.viewport(width, 900);
  document.documentElement.classList.toggle("dark", dark);
  vi.spyOn(newspaperApi, "story").mockResolvedValue(detail);
  const onFollowUp = vi.fn();
  const onOpen = vi.fn();
  const screen = await mount({ story: "story-bandung", onFollowUp, onOpen });

  await expect
    .element(screen.getByRole("heading", { level: 1, name: "Revenue fell 34.6% in Bandung" }))
    .toBeVisible();
  for (const name of ["What happened", "Why it matters", "What to check", "Where it moved", "Business rules"])
    await expect.element(screen.getByRole("heading", { level: 2, name })).toBeVisible();
  await expect.element(screen.getByText("Whole view")).toBeVisible();
  await expect
    .element(screen.getByText(/Written by the default model from verified figures/))
    .toBeVisible();
  await expect.element(screen.getByText(/must be explained in the weekly review/)).toBeVisible();
  await screen.getByRole("button", { name: "Ask Studio about this" }).click();
  expect(onFollowUp.mock.calls[0][0]).toContain("Revenue fell 34.6% in Bandung");
  await screen.getByRole("button", { name: "Back to News" }).click();
  expect(onOpen).toHaveBeenCalledWith();
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
  await page.screenshot();
});

it("does not reveal whether a hidden story exists", async () => {
  vi.spyOn(newspaperApi, "story").mockRejectedValue(new ApiError(404, "Record unavailable"));
  const screen = await mount({ story: "story-bandung" });

  await expect
    .element(screen.getByText("This story is not available with your current access"))
    .toBeVisible();
  expect(screen.getByText(/Bandung/).query()).toBeNull();
});
