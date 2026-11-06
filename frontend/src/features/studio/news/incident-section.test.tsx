import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { IncidentSection } from "./incident-section";
import { newspaperApi, type Incident } from "./newspaper-api";
import "@/styles/index.css";

const window = { start: "2026-11-05T17:00:00Z", end: "2026-11-06T17:00:00Z" };

const incident: Incident = {
  id: "inc_1",
  revision: 3,
  status: "INVESTIGATING",
  severity: "critical",
  causal_status: "unknown",
  note: "Grouped signals are related observations. No cause has been established.",
  signals: [
    {
      id: "sig_rev",
      source: { type: "NEWSROOM_STORY", id: "story-bandung" },
      semantic_view_id: "sales",
      metric: "net_revenue",
      concept: { id: "con_rev", qualified_name: "Finance.NetRevenue", label: "Net revenue" },
      binding: "BOUND",
      direction: "decrease",
      severity: "warning",
      window,
    },
    {
      id: "sig_ref",
      source: { type: "NEWSROOM_STORY", id: "story-refunds" },
      semantic_view_id: "sales",
      metric: "refund_rate",
      concept: { id: "con_ref", qualified_name: "Finance.RefundRate", label: "Refund rate" },
      binding: "BOUND",
      direction: "increase",
      severity: "critical",
      window,
    },
  ],
  grouping: [
    { signal_id: "sig_rev", rule: "opened", with_signal_id: null },
    { signal_id: "sig_ref", rule: "related_concepts", with_signal_id: "sig_rev" },
  ],
  transitions: [
    {
      from_status: "OPEN",
      to_status: "INVESTIGATING",
      principal: "ana",
      reason: "Investigation opened in Studio from News",
      at: "2026-11-06T08:00:00Z",
    },
  ],
  updates: [
    {
      id: "upd_1",
      revision: 2,
      status: "APPROVED",
      summary: "Refunds explain part of the decline",
      claims: [{ statement: "Refunds rose in the same accounts", causal_status: "association" }],
      author: "ana",
      can_review: false,
      review: { principal: "paula", decision: "APPROVE", rationale: "Checked", at: "2026-11-06T09:00:00Z" },
      created_at: "2026-11-06T08:30:00Z",
    },
    {
      id: "upd_2",
      revision: 1,
      status: "PENDING_REVIEW",
      summary: "Discount mix shifted",
      claims: [{ statement: "Discounts rose", causal_status: "arithmetic" }],
      author: "ana",
      can_review: true,
      review: null,
      created_at: "2026-11-06T10:00:00Z",
    },
  ],
};

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IncidentSection storyId="story-bandung" epoch={0} />
    </QueryClientProvider>,
  );
}

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
});

it("shows related signals, reviewed findings and the correlation caveat", async () => {
  vi.spyOn(newspaperApi, "incident").mockResolvedValue({ incident });
  const screen = await mount();

  await expect.element(screen.getByText("Investigating", { exact: true })).toBeVisible();
  await expect
    .element(screen.getByText("Investigating by ana: Investigation opened in Studio from News"))
    .toBeVisible();
  await expect.element(screen.getByText("1 related signal you can see")).toBeVisible();
  await expect.element(screen.getByText("Finance.RefundRate")).toBeVisible();
  await expect
    .element(screen.getByText("rose, linked business concepts as Finance.NetRevenue"))
    .toBeVisible();
  await expect.element(screen.getByText(/No cause has been established/)).toBeVisible();
  await expect.element(screen.getByText("Refunds explain part of the decline")).toBeVisible();
  await expect.element(screen.getByText("(Moves together; not a cause)")).toBeVisible();
  await expect.element(screen.getByText("Found by ana, reviewed by paula")).toBeVisible();
  await expect.element(screen.getByText("Waiting for review")).toBeVisible();
});

it("lets a reviewer in the same role approve a pending finding", async () => {
  vi.spyOn(newspaperApi, "incident").mockResolvedValue({ incident });
  const review = vi.spyOn(newspaperApi, "reviewUpdate").mockResolvedValue({
    ...incident,
    updates: incident.updates.map((item) =>
      item.id === "upd_2" ? { ...item, status: "APPROVED" as const, can_review: false } : item,
    ),
  });
  const screen = await mount();

  await screen.getByRole("button", { name: "Approve finding" }).click();
  await screen.getByLabelText("What you checked").fill("Matches the discount report");
  await screen.getByRole("button", { name: "Approve", exact: true }).click();
  await expect.poll(() => review.mock.calls.length).toBe(1);
  expect(review.mock.calls[0][0]).toBe("inc_1");
  expect(review.mock.calls[0][1]).toBe("upd_2");
  expect(review.mock.calls[0][2]).toMatchObject({
    decision: "APPROVE",
    rationale: "Matches the discount report",
  });
  await expect.element(screen.getByText("Discount mix shifted")).toBeVisible();
  expect(screen.getByText("Waiting for review").query()).toBeNull();
});

it("explains a story with no incident and a failed read", async () => {
  vi.spyOn(newspaperApi, "incident").mockResolvedValueOnce({ incident: null });
  const empty = await mount();
  await expect.element(empty.getByText(/No related signals yet/)).toBeVisible();
  await cleanup();

  vi.spyOn(newspaperApi, "incident").mockRejectedValue(new Error("boom"));
  const failed = await mount();
  await expect.element(failed.getByText("The incident could not be loaded.")).toBeVisible();
});
