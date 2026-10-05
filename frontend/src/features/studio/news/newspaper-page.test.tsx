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
    impact: "unfavorable",
    highlights: [
      { text: "IDR 134,621,612", kind: "change" },
      { text: "IDR 254,505,601", kind: "figure" },
      { text: "Revenue", kind: "subject" },
      { text: "Bandung", kind: "subject" },
    ],
    reaction: null,
    score: 2.3,
    head: true,
    reason: "Largest move in this edition",
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
  impact: "favorable",
  score: 1.1,
  head: false,
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
  await expect.element(screen.getByText("−34.6% (−IDR 134.6M)").first()).toBeVisible();
  await expect
    .element(screen.getByRole("heading", { level: 3, name: "Revenue rose 14.3% in Online" }))
    .toBeVisible();
  await expect.element(screen.getByText("2 stories")).toBeVisible();
  await expect.element(screen.getByText("1 critical")).toBeVisible();
  // One long column: every story is its own block, divided from the one before.
  const entries = [...document.querySelectorAll('[data-slot="news-entry"]')];
  expect(document.body.textContent).not.toMatch(/\b0[12]\b/);
  await expect.element(screen.getByText("Top story for you")).toBeVisible();
  await expect.element(screen.getByText(/Largest move in this edition/)).toBeVisible();
  // The head figure leads each story; its change is coloured by business impact.
  const tones = [...document.querySelectorAll("[data-tone]")].map((node) => [
    node.getAttribute("data-tone"),
    node.className.includes("text-destructive"),
    node.className.includes("text-success-strong"),
  ]);
  expect(tones).toEqual([
    ["bad", true, false],
    ["good", false, true],
  ]);
  expect(entries[0].querySelector('[data-highlight="subject"]')?.textContent).toBe("Revenue");
  expect(
    [...document.querySelectorAll("[data-chart-kind]")].map((node) =>
      node.getAttribute("data-chart-kind"),
    ),
  ).toEqual(["weekday"]);
  expect(getComputedStyle(entries[0]).borderTopWidth).toBe("0px");
  expect(getComputedStyle(entries[1]).borderTopWidth).toBe("1px");
  expect(entries[1].getBoundingClientRect().top).toBeGreaterThan(
    entries[0].getBoundingClientRect().bottom - 1,
  );
  await expect.poll(() => document.querySelectorAll('[data-testid="news-chart"] svg').length).toBe(2);
  await screen
    .getByRole("button", { name: "Revenue fell 34.6% in Bandung", exact: true })
    .click();
  expect(onOpen).toHaveBeenCalledWith("story-bandung");
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
  for (const element of document.querySelectorAll("article, figure"))
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

it("colours by direction when the metric has not been judged", async () => {
  vi.spyOn(newspaperApi, "read").mockResolvedValue({
    ...paper,
    sections: [{ ...paper.sections[0], stories: [story({ impact: null })] }],
  });
  await mount();

  await expect.poll(() => document.querySelector("[data-tone]")?.getAttribute("data-tone")).toBe("bad");
});

it("rotates the chart form down the page", async () => {
  const many = [0, 1, 2, 3].map((index) =>
    story({
      id: `s${index}`,
      score: 9 - index,
      head: index === 0,
      slice: { dimension: "city", value: `City ${index}` },
      narrative: { ...story().narrative, headline: `Story ${index}` },
    }),
  );
  vi.spyOn(newspaperApi, "read").mockResolvedValue({
    ...paper,
    sections: [{ ...paper.sections[0], stories: many }],
  });
  await mount();

  await expect
    .poll(() =>
      [...document.querySelectorAll("[data-chart-kind]")].map((node) =>
        node.getAttribute("data-chart-kind"),
      ),
    )
    .toEqual(["weekday", "trend", "level"]);
  await expect.poll(() => document.querySelectorAll('[data-testid="news-chart"] svg').length).toBe(4);
});

it("records a like or dislike without opening the story, and clears it on a second press", async () => {
  vi.spyOn(newspaperApi, "read").mockResolvedValue(paper);
  const react = vi
    .spyOn(newspaperApi, "react")
    .mockImplementation(async (id, reaction) => ({ id, reaction }));
  const onOpen = vi.fn();
  const screen = await mount({ onOpen });

  const like = screen.getByRole("button", { name: "More like this: Revenue fell 34.6% in Bandung" });
  await expect.element(like).toHaveAttribute("aria-pressed", "false");
  await like.click();
  await expect.element(like).toHaveAttribute("aria-pressed", "true");
  expect(react).toHaveBeenLastCalledWith("story-bandung", "like");

  await screen
    .getByRole("button", { name: "Less like this: Revenue fell 34.6% in Bandung" })
    .click();
  expect(react).toHaveBeenLastCalledWith("story-bandung", "dislike");
  await expect.element(like).toHaveAttribute("aria-pressed", "false");

  await screen
    .getByRole("button", { name: "Less like this: Revenue fell 34.6% in Bandung" })
    .click();
  expect(react).toHaveBeenLastCalledWith("story-bandung", null);
  expect(onOpen).not.toHaveBeenCalled();
});

it("puts the mark back when a reaction cannot be saved", async () => {
  vi.spyOn(newspaperApi, "read").mockResolvedValue(paper);
  vi.spyOn(newspaperApi, "react").mockRejectedValue(new ApiError(500, "Down"));
  const screen = await mount();

  const like = screen.getByRole("button", { name: "More like this: Revenue fell 34.6% in Bandung" });
  await like.click();

  await expect.element(screen.getByRole("alert")).toHaveTextContent("Your reaction could not be saved");
  await expect.element(like).toHaveAttribute("aria-pressed", "false");
});

it("orders stories the way the server ranked them for this reader", async () => {
  vi.spyOn(newspaperApi, "read").mockResolvedValue({
    ...paper,
    sections: [
      {
        ...paper.sections[0],
        stories: [
          story({ score: 0.4, head: false, reaction: "dislike" }),
          { ...online, score: 3.2, head: true, reason: "Matches stories you liked: Channel · Online" },
        ],
      },
    ],
  });
  const screen = await mount();

  await expect
    .element(screen.getByRole("heading", { level: 2, name: "Revenue rose 14.3% in Online" }))
    .toBeVisible();
  await expect.element(screen.getByText(/Matches stories you liked: Channel · Online/)).toBeVisible();
  await expect
    .element(screen.getByRole("button", { name: "Less like this: Revenue fell 34.6% in Bandung" }))
    .toHaveAttribute("aria-pressed", "true");
});

it("names each story's desk and narrows the edition to one desk", async () => {
  const overtime = story({
    id: "story-overtime",
    view_id: "workforce",
    view_name: "news_workforce",
    metric: "overtime_hours",
    metric_label: "Overtime hours",
    unit: "hours",
    after: 312.4,
    before: 215.1,
    change: 97.3,
    relative_change: 0.452,
    impact: "unfavorable",
    score: 2.1,
    head: false,
    narrative: { ...story().narrative, headline: "Overtime hours rose 45.2% in Bandung" },
  });
  vi.spyOn(newspaperApi, "read").mockResolvedValue({
    edition_date: "2026-10-04",
    sections: [
      { ...paper.sections[0], stories: [story({ view_name: "news_retail_sales" })] },
      {
        view_id: "workforce",
        name: "news_workforce",
        edition_date: "2026-10-04",
        pressed_at: "2026-10-05T02:05:00Z",
        stories: [overtime],
      },
    ],
  });
  const screen = await mount();

  await expect.element(screen.getByText("2 views covered", { exact: false })).toBeVisible();
  await expect.element(screen.getByText("News workforce ·")).toBeVisible();
  await expect.element(screen.getByText("312.4 hours")).toBeVisible();
  // A rise in overtime is bad for the business, so it is not coloured as a gain.
  const tones = [...document.querySelectorAll("[data-tone]")].map((node) =>
    node.getAttribute("data-tone"),
  );
  expect(tones).toEqual(["bad", "bad"]);

  await screen.getByRole("button", { name: /News workforce\s*1/ }).click();

  expect(document.querySelectorAll('[data-slot="news-entry"]').length).toBe(1);
  await expect
    .element(screen.getByRole("heading", { level: 2, name: "Overtime hours rose 45.2% in Bandung" }))
    .toBeVisible();
  expect(screen.getByText("Top story for you").query()).toBeNull();
  await expect.element(screen.getByText("1 story")).toBeVisible();

  await screen.getByRole("button", { name: /All desks/ }).click();
  expect(document.querySelectorAll('[data-slot="news-entry"]').length).toBe(2);
  await expect.element(screen.getByText("Top story for you")).toBeVisible();
});

it("offers no desk filter when the edition has one desk", async () => {
  vi.spyOn(newspaperApi, "read").mockResolvedValue(paper);
  const screen = await mount();

  await expect.element(screen.getByText("Edition at a glance")).toBeVisible();
  expect(screen.getByRole("group", { name: "Desks" }).query()).toBeNull();
});

it("opens a story from anywhere in its block", async () => {
  vi.spyOn(newspaperApi, "read").mockResolvedValue(paper);
  const onOpen = vi.fn();
  const screen = await mount({ onOpen });

  await screen.getByText("Channel · Online").click();

  expect(onOpen).toHaveBeenCalledExactlyOnceWith("story-online");
});

it("moves between edition days from the masthead", async () => {
  const read = vi.spyOn(newspaperApi, "read").mockResolvedValue(paper);
  const onEdition = vi.fn();
  const screen = await mount({ onEdition });

  await screen.getByRole("button", { name: "Previous day" }).click();
  expect(onEdition).toHaveBeenCalledWith("2026-10-03");
  expect(read.mock.calls[0][0]).toBeUndefined();

  await cleanup();
  read.mockResolvedValue({ edition_date: null, sections: [] });
  const past = await mount({ onEdition, edition: "2026-09-30" });
  await expect.element(past.getByText("No edition for this day")).toBeVisible();
  expect(read.mock.calls[read.mock.calls.length - 1][0]).toBe("2026-09-30");
  await past.getByRole("button", { name: "Next day" }).click();
  expect(onEdition).toHaveBeenCalledWith("2026-10-01");
  await past.getByRole("button", { name: "Latest edition" }).click();
  expect(onEdition).toHaveBeenLastCalledWith();
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
])("tells the full story in a panel beside the edition at $width px", async ({ width, dark }) => {
  await page.viewport(width, 900);
  document.documentElement.classList.toggle("dark", dark);
  vi.spyOn(newspaperApi, "read").mockResolvedValue(paper);
  vi.spyOn(newspaperApi, "story").mockResolvedValue(detail);
  const onFollowUp = vi.fn();
  const onOpen = vi.fn();
  const screen = await mount({ story: "story-bandung", onFollowUp, onOpen });

  const panel = screen.getByRole("dialog", { name: "Revenue fell 34.6% in Bandung" });
  await expect.element(panel).toBeVisible();
  // The edition stays mounted behind the panel, so the reader keeps their place.
  expect(document.querySelectorAll('[data-slot="news-entry"]').length).toBe(2);
  for (const name of ["What happened", "Why it matters", "What to check", "Where it moved", "Business rules"])
    await expect.element(panel.getByRole("heading", { level: 3, name })).toBeVisible();
  await expect.element(panel.getByText("Whole view")).toBeVisible();
  await expect.element(panel.getByText("1 of 2")).toBeVisible();
  await expect
    .element(panel.getByRole("button", { name: "More like this: Revenue fell 34.6% in Bandung" }))
    .toBeVisible();
  await expect.element(panel.getByRole("button", { name: "Previous" })).toBeDisabled();
  await panel.getByRole("button", { name: "Next" }).click();
  expect(onOpen).toHaveBeenLastCalledWith("story-online");
  await expect
    .element(screen.getByText(/Written by the default model from verified figures/))
    .toBeVisible();
  await expect.element(screen.getByText(/must be explained in the weekly review/)).toBeVisible();
  await screen.getByRole("button", { name: "Ask Studio about this" }).click();
  expect(onFollowUp.mock.calls[0][0]).toBe(
    "From News: Revenue fell 34.6% in Bandung. What could explain this change, and what should I check first?",
  );
  await screen.getByRole("button", { name: "Close" }).click();
  expect(onOpen).toHaveBeenLastCalledWith();
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
  await page.screenshot();
});

it("does not reveal whether a hidden story exists", async () => {
  vi.spyOn(newspaperApi, "read").mockResolvedValue({
    ...paper,
    sections: [{ ...paper.sections[0], stories: [] }],
  });
  vi.spyOn(newspaperApi, "story").mockRejectedValue(new ApiError(404, "Record unavailable"));
  const screen = await mount({ story: "story-bandung" });

  await expect
    .element(screen.getByText("This story is not available with your current access"))
    .toBeVisible();
  expect(screen.getByText(/Bandung/).query()).toBeNull();
});
