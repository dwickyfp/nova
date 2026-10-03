import { afterEach, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { ApiError } from "@/lib/api-client";
import {
  intelligenceApi,
  type Decision,
  type News,
} from "@/features/intelligence/lifecycle-api";
import { StudioIntelligence } from "./studio-intelligence";
import "@/styles/index.css";

const ref = { view_id: "finance", version: 1, fingerprint: "pinned" };
const window = { start: "2026-03-20T00:00:00Z", end: "2026-03-21T00:00:00Z" };
const news: News = {
  id: "news-1",
  revision: 1,
  created_at: window.end,
  title: "Jakarta sales declined",
  summary: "A material change requires investigation.",
  status: "open",
  semantic: ref,
  evidence: [],
  before: 100,
  after: 60,
  change: -40,
  severity: "warning",
  confidence: { label: "medium", dimension: "detection" },
  monitor_id: "monitor",
  window,
};
const decision: Decision = {
  id: "decision-1",
  revision: 2,
  created_at: window.end,
  title: "Jakarta stock availability",
  status: "awaiting_approval",
  semantic: ref,
  evidence: [],
  currency: "IDR",
  selected_option_id: "transfer",
  policy: {
    decision: "REQUIRE_APPROVAL",
    reason: "Cost requires review.",
    policy_revision: 1,
  },
  outcome_window: window,
  options: [
    {
      id: "transfer",
      description: "Transfer available stock",
      action_type: "inventory_transfer",
      prediction: 90,
      lower_bound: 75,
      upper_bound: 100,
      cost: 5,
      incremental_gross_profit: 20,
      feasible: true,
      risk: "medium",
      method: "conditional-economics-v1",
      assumptions: { baseline_units: 60 },
    },
  ],
};

function mount(props: Partial<Parameters<typeof StudioIntelligence>[0]> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <div className="flex h-dvh min-w-0 flex-col">
        <StudioIntelligence
          view="news"
          onOpen={vi.fn()}
          onDecision={vi.fn()}
          onFollowUp={vi.fn()}
          {...props}
        />
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
])(
  "opens scoped News at $width px without page overflow",
  async ({ width, dark }) => {
    await page.viewport(width, 700);
    document.documentElement.classList.toggle("dark", dark);
    vi.spyOn(intelligenceApi, "page").mockResolvedValue({
      items: [news],
      next_after: null,
    });
    const onOpen = vi.fn();
    const screen = await mount({ onOpen });
    await screen
      .getByRole("button", { name: /Jakarta sales declined/ })
      .click();
    expect(onOpen).toHaveBeenCalledWith("news-1");
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
    await page.screenshot();
  },
);

it("removes cached News when the principal changes, including same-role users", async () => {
  useAuthStore
    .getState()
    .auth.setUser({
      username: "alice",
      activeRole: "ANALYST",
      roles: ["ANALYST"],
    });
  const listing = vi
    .spyOn(intelligenceApi, "page")
    .mockResolvedValue({ items: [news], next_after: null });
  const screen = await mount();
  await expect
    .element(screen.getByRole("button", { name: /Jakarta sales declined/ }))
    .toBeVisible();
  listing.mockRejectedValue(new ApiError(403, "Denied"));
  useAuthStore
    .getState()
    .auth.setUser({
      username: "bob",
      activeRole: "ANALYST",
      roles: ["ANALYST"],
    });
  await expect
    .element(screen.getByRole("button", { name: /Jakarta sales declined/ }))
    .not.toBeInTheDocument();
  await expect
    .element(
      screen.getByText("This item is unavailable with your current access"),
    )
    .toBeVisible();
});

it("requires the current policy and reviewer permission before approval", async () => {
  vi.spyOn(intelligenceApi, "get").mockResolvedValue(decision);
  vi.spyOn(intelligenceApi, "policy").mockResolvedValue({
    current: false,
    policy_revision: 2,
    can_edit: false,
    can_review: true,
  });
  vi.spyOn(intelligenceApi, "lineage").mockResolvedValue({
    news,
    investigation: { id: "investigation" } as never,
    decision,
    evidence: [],
    events: [],
    outcomes: [],
  });
  const operate = vi.spyOn(intelligenceApi, "operate");
  const screen = await mount({ view: "decisions", item: decision.id });
  await expect.element(screen.getByText(/Policy changed/)).toBeVisible();
  await expect
    .element(screen.getByRole("button", { name: "Approve this revision" }))
    .not.toBeInTheDocument();
  await expect
    .element(screen.getByRole("button", { name: "Select option" }))
    .not.toBeInTheDocument();
  expect(operate).not.toHaveBeenCalled();
});

it("submits approval for the displayed decision revision", async () => {
  vi.spyOn(intelligenceApi, "get").mockResolvedValue(decision);
  vi.spyOn(intelligenceApi, "policy").mockResolvedValue({
    current: true,
    policy_revision: 1,
    can_edit: false,
    can_review: true,
  });
  vi.spyOn(intelligenceApi, "lineage").mockResolvedValue({
    news,
    investigation: { id: "investigation" } as never,
    decision,
    evidence: [],
    events: [],
    outcomes: [],
  });
  const operate = vi
    .spyOn(intelligenceApi, "operate")
    .mockResolvedValue({ ...decision, status: "approved", revision: 3 });
  const screen = await mount({ view: "decisions", item: decision.id });
  await screen.getByRole("button", { name: "Approve this revision" }).click();
  expect(operate).toHaveBeenCalledWith(decision, "approve", undefined);
});

it("shows an empty News state without creating a schedule", async () => {
  vi.spyOn(intelligenceApi, "page").mockResolvedValue({
    items: [],
    next_after: null,
  });
  const screen = await mount();
  await expect
    .element(screen.getByText("No material changes to review"))
    .toBeVisible();
});
