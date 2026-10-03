import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { ThemeProvider } from "@/context/theme-provider";
import { Main } from "@/components/layout/main";
import { MonitoringPageScroller } from "../index";
import { autopilot, type Overview, type RecordItem } from "./api";
import { QueryAutopilot } from "./index";
import "@/styles/index.css";
vi.mock("./api", () => ({
  autopilot: {
    overview: vi.fn(),
    list: vi.fn(),
    detail: vi.fn(),
    related: vi.fn(),
    mutate: vi.fn(),
    policy: vi.fn(),
    enroll: vi.fn(),
  },
}));
const overview: Overview = {
  policy: {
    id: "default",
    version: 3,
    mode: "GOVERNED",
    collection_enabled: true,
    profile_sample_rate: 0.01,
    regression_ratio: 1.5,
    regression_absolute_ms: 100,
    absolute_slow_ms: 5000,
    high_frequency: 100,
    minimum_gain: 0.1,
    observations_days: 7,
    payload_hours: 24,
    history_days: 90,
    max_statistics_tables: 1,
  },
  counts: {
    families: 2,
    incidents: 1,
    opportunities: 1,
    experiments: 1,
    actions: 0,
  },
  collection: {
    enabled: true,
    queued: 0,
    capacity: 2048,
    dropped: 0,
    persisted: 100,
    failed_batches: 0,
  },
  latency_basis: "nova_total_ms",
};
const candidate: RecordItem = {
  id: "candidate-1",
  family_id: "family-1",
  cohort_id: "cohort-1",
  kind: "HISTOGRAM",
  state: "READY_APPROVAL",
  version: 4,
  policy_version: 3,
  targets: ["retail.orders"],
  parameters: { columns: ["customer_id"], buckets: 64 },
  priority: {
    score: 67,
    contributions: { workload_impact: 20, risk: -10 },
    estimated_gain: 0.3,
    estimate_source: "heuristic",
    historical_outcomes: 0,
  },
};
let client: QueryClient;
beforeEach(() => {
  vi.resetAllMocks();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  useAuthStore.getState().auth.setUser({
    username: "admin",
    roles: ["ACCOUNTADMIN"],
    activeRole: "ACCOUNTADMIN",
    securityContextVersion: 1,
  });
  vi.mocked(autopilot.overview).mockResolvedValue(overview);
  vi.mocked(autopilot.list).mockResolvedValue({
    items: [candidate],
    next_cursor: null,
  });
  vi.mocked(autopilot.detail).mockResolvedValue(candidate);
  vi.mocked(autopilot.related).mockResolvedValue({
    items: [
      {
        id: "e1",
        kind: "profile",
        availability: "expired",
        reason: "retention_expired",
      },
    ],
    next_cursor: null,
  });
  vi.mocked(autopilot.mutate).mockResolvedValue({
    id: "operation-1",
    state: "QUEUED",
  });
});
afterEach(async () => {
  client.clear();
  document.documentElement.classList.remove("dark");
  await page.viewport(1280, 800);
});
async function mount(dark = false, assistant = false) {
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider
        defaultTheme={dark ? "dark" : "light"}
        storageKey="autopilot-test-theme"
      >
        <div
          style={{ height: "100dvh" }}
          className="flex min-h-0 min-w-0 bg-background text-foreground"
        >
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <Main fixed fluid className="min-h-0 p-0">
              <MonitoringPageScroller>
                <QueryAutopilot />
              </MonitoringPageScroller>
            </Main>
          </div>
          {assistant && (
            <aside style={{ width: "35%" }} className="shrink-0 border-l p-3">
              Assistant
            </aside>
          )}
        </div>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
async function openCandidate() {
  await page.getByRole("tab", { name: "Opportunities", exact: true }).click();
  await page.getByRole("button", { name: "histogram candidate-1" }).click();
}
it("binds approval to the displayed version and distinguishes estimates from evidence", async () => {
  const view = await mount();
  await openCandidate();
  await expect
    .element(view.getByText("Candidate version 4 · Policy version 3"))
    .toBeVisible();
  await expect
    .element(view.getByText(/Expected gain is an estimate/))
    .toBeVisible();
  await expect
    .element(view.getByText("expired", { exact: true }))
    .toBeVisible();
  await page.getByRole("button", { name: "Approve this version" }).click();
  expect(autopilot.mutate).toHaveBeenCalledWith(
    "candidate-1",
    "approve",
    4,
    expect.any(String),
  );
});
it("blocks expired approvals and hides mutations after role switch", async () => {
  vi.mocked(autopilot.detail).mockResolvedValue({
    ...candidate,
    state: "APPROVED",
    approval_expires_at: "2020-01-01T00:00:00Z",
  });
  const view = await mount();
  await openCandidate();
  await expect
    .element(page.getByRole("button", { name: "Apply validated change" }))
    .toBeDisabled();
  await expect.element(view.getByText(/Approval expired/)).toBeVisible();
  useAuthStore.getState().auth.setUser({
    username: "admin",
    roles: ["MONITORADMIN"],
    activeRole: "MONITORADMIN",
    securityContextVersion: 2,
  });
  await expect
    .element(page.getByRole("button", { name: "Apply validated change" }))
    .not.toBeInTheDocument();
  await expect
    .poll(() =>
      client
        .getQueryCache()
        .getAll()
        .some(
          (q) => q.queryKey.includes("MONITORADMIN") && q.queryKey.includes(2),
        ),
    )
    .toBe(true);
});
it("supports keyboard navigation, empty and error states", async () => {
  vi.mocked(autopilot.list).mockResolvedValue({ items: [], next_cursor: null });
  const view = await mount();
  await expect
    .element(view.getByRole("tab", { name: "Overview", exact: true }))
    .toBeVisible();
  const tab = view.container.querySelector(
    '[role="tab"][data-state="active"]',
  ) as HTMLElement;
  tab.focus();
  await userEvent.keyboard("{ArrowRight}");
  await expect
    .element(view.getByRole("tab", { name: "Families", exact: true }))
    .toHaveAttribute("aria-selected", "true");
  await expect.element(view.getByText(/No families/i)).toBeVisible();
  vi.mocked(autopilot.overview).mockRejectedValue(
    new Error("Monitoring access unavailable"),
  );
  await page.getByRole("button", { name: "Refresh Autopilot" }).click();
  await expect
    .element(view.getByText("Autopilot is unavailable"))
    .toBeVisible();
});
it.each([false, true])(
  "fits mobile and assistant layouts in dark=%s",
  async (dark) => {
    await page.viewport(390, 844);
    const view = await mount(dark);
    await expect
      .element(view.getByRole("heading", { name: "Query Autopilot" }))
      .toBeVisible();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
      window.innerWidth + 1,
    );
    await openCandidate();
    await expect
      .element(view.getByRole("heading", { name: "Proposed change" }))
      .toBeVisible();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
      window.innerWidth + 1,
    );
    await view.unmount();
    await page.viewport(1280, 800);
    const wide = await mount(dark, true);
    await expect
      .element(wide.getByRole("heading", { name: "Query Autopilot" }))
      .toBeVisible();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
      window.innerWidth + 1,
    );
  },
);

it("shows structural plan changes with planner estimates and measured outcomes", async () => {
  const before = {
    ordinal: 0,
    operator: "OlapScanNode",
    estimates: { cardinality: 50000 },
    attributes: { rollup: "orders" },
  };
  const after = {
    ordinal: 0,
    operator: "OlapScanNode",
    estimates: { cardinality: 30 },
    attributes: { rollup: "nova_ap_trial" },
  };
  vi.mocked(autopilot.detail).mockResolvedValue({
    ...candidate,
    plans: { before: { operators: [before] }, after: { operators: [after] } },
    plan_diff: [{ ordinal: 0, before, after }],
    result: {
      status: "INCONCLUSIVE",
      correctness: "EQUIVALENT",
      improvement: null,
      reason: "materialized_view_rewrite_or_freshness_unproven",
      repetitions: 30,
      before: { count: 30, mean_ms: 100, stddev_ms: 5 },
      after: { count: 30, mean_ms: 50, stddev_ms: 3 },
      controls: {
        before: { count: 30, mean_ms: 20 },
        after: { count: 30, mean_ms: 20 },
      },
    },
  });
  const view = await mount();
  await openCandidate();
  await expect
    .element(view.getByText(/1 operator positions changed/))
    .toBeVisible();
  await expect.element(view.getByText("rollup: nova_ap_trial")).toBeVisible();
  await expect.element(view.getByText(/30 measured repetitions/)).toBeVisible();
  expect(view.container.textContent).toContain("planner estimates");
  expect(view.container.textContent).toContain(
    "Measured improvement: Unavailable",
  );
});
