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
import { DecisionDetail, StudioIntelligence } from "./studio-intelligence";
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
  useAuthStore.getState().auth.setUser({
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
  useAuthStore.getState().auth.setUser({
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

it.each([
  { width: 320, dark: true },
  { width: 1280, dark: false },
])(
  "renders generic scenario effects and optional legacy profit at $width px",
  async ({ width, dark }) => {
    await page.viewport(width, 700);
    document.documentElement.classList.toggle("dark", dark);
    const generic: Decision = {
      ...decision,
      currency: null,
      options: [
        {
          ...decision.options[0],
          incremental_gross_profit: undefined,
          effects: {
            capacity_delta: 12,
            feasible_capacity: true,
            explanation: "Stated capacity assumption",
          },
        },
      ],
    };
    vi.spyOn(intelligenceApi, "get").mockResolvedValue(generic);
    vi.spyOn(intelligenceApi, "policy").mockResolvedValue({
      current: true,
      policy_revision: 1,
      can_edit: false,
      can_review: false,
    });
    vi.spyOn(intelligenceApi, "lineage").mockResolvedValue({
      news,
      investigation: { id: "investigation" } as never,
      decision: generic,
      evidence: [],
      events: [],
      outcomes: [],
    });
    const screen = await mount({ view: "decisions", item: generic.id });
    await expect
      .element(
        screen.getByRole("heading", { name: "Additional scenario results" }),
      )
      .toBeVisible();
    await expect
      .element(screen.getByText("capacity delta", { exact: true }))
      .toBeVisible();
    await expect.element(screen.getByText("12", { exact: true })).toBeVisible();
    await expect
      .element(screen.getByText("true", { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByText("Stated capacity assumption", { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByText(/gross profit/))
      .not.toBeInTheDocument();
    await expect
      .element(screen.getByText(/Monetary estimates/))
      .not.toBeInTheDocument();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
  },
);

it("displays existing gross profit once when canonical effects include the legacy result", async () => {
  vi.spyOn(intelligenceApi, "get").mockResolvedValue({
    ...decision,
    options: [
      { ...decision.options[0], effects: { incremental_gross_profit: 20 } },
    ],
  });
  vi.spyOn(intelligenceApi, "policy").mockResolvedValue({
    current: true,
    policy_revision: 1,
    can_edit: false,
    can_review: false,
  });
  vi.spyOn(intelligenceApi, "lineage").mockResolvedValue({
    news,
    investigation: { id: "investigation" } as never,
    decision,
    evidence: [],
    events: [],
    outcomes: [],
  });
  const screen = await mount({ view: "decisions", item: decision.id });
  await expect
    .element(screen.getByText("incremental gross profit", { exact: true }))
    .toBeVisible();
  expect(
    Array.from(document.querySelectorAll("dt")).filter(
      (item) => item.textContent === "incremental gross profit",
    ),
  ).toHaveLength(1);
});

it("passes explicit Mission context to a governed Decision operation", async () => {
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
  const operate = vi.spyOn(intelligenceApi, "operate").mockResolvedValue({
    ...decision,
    revision: 3,
    status: "approved",
  });
  const screen = await render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false } },
        })
      }
    >
      <DecisionDetail
        decision={decision}
        refresh={vi.fn()}
        epoch={1}
        missionId="mission-resumed"
      />
    </QueryClientProvider>,
  );
  await screen.getByRole("button", { name: "Approve this revision" }).click();
  expect(operate).toHaveBeenCalledWith(
    decision,
    "approve",
    undefined,
    "mission-resumed",
  );
  expect(intelligenceApi.lineage).toHaveBeenCalledWith(
    decision.id,
    "mission-resumed",
  );
  expect(intelligenceApi.policy).toHaveBeenCalledWith(
    decision.id,
    "mission-resumed",
  );
});

it("forwards Mission context when observing the selected Decision outcome", async () => {
  const selected = { ...decision, status: "approved" };
  vi.spyOn(intelligenceApi, "policy").mockResolvedValue({
    current: true,
    policy_revision: 1,
    can_edit: true,
    can_review: false,
  });
  vi.spyOn(intelligenceApi, "lineage").mockResolvedValue({
    news,
    investigation: { id: "investigation" } as never,
    decision: selected,
    evidence: [],
    events: [],
    outcomes: [],
  });
  const outcome = vi.spyOn(intelligenceApi, "outcome").mockResolvedValue({
    id: "outcome-1",
    revision: 1,
    created_at: window.end,
    status: "pending",
    semantic: ref,
    evidence: [],
    predicted: 90,
    completeness: 0,
    attribution: "unknown",
    dimensions: {},
  });
  const screen = await render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false } },
        })
      }
    >
      <DecisionDetail
        decision={selected}
        refresh={vi.fn()}
        epoch={1}
        missionId="mission-resumed"
      />
    </QueryClientProvider>,
  );
  await screen.getByRole("button", { name: "Evaluate outcome" }).click();
  expect(outcome).toHaveBeenCalledWith(decision.id, "mission-resumed");
});
