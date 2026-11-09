import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fetchAlerts,
  fetchReadiness,
  type AlertsResponse,
  type ReadinessResponse,
} from "./api";
import { MonitoringHealth } from "./index";
import "@/styles/index.css";

vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  fetchAlerts: vi.fn(),
  fetchReadiness: vi.fn(),
}));

const alerts: AlertsResponse = {
  summary: {
    overall: "warning",
    firing: 1,
    unknown: 0,
    counts: { critical: 0, warning: 1 },
  },
  alerts: [
    {
      id: "fe-heap",
      title: "Frontend heap pressure",
      category: "memory",
      severity: "warning",
      status: "firing",
      value: 91,
      threshold: "> 85%",
      description: "Frontend heap usage is high.",
      detail: "heap 91%",
    },
  ],
};

const readiness: ReadinessResponse = {
  metadata: {
    cluster_id: "c1",
    version: "3.4",
    generated_at: "2026-11-09T00:00:00Z",
    engine_reachable: true,
  },
  findings: [
    {
      category: "replication",
      name: "Replica count",
      status: "PASS",
      detail: "Three replicas configured.",
    },
  ],
  counts: { PASS: 1 },
  result: "READY",
};

let client: QueryClient;
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
});
afterEach(() => {
  client.clear();
  vi.resetAllMocks();
});

function mount() {
  return render(
    <QueryClientProvider client={client}>
      <MonitoringHealth />
    </QueryClientProvider>,
  );
}

it("shows alert rules while the slower readiness audit is still loading", async () => {
  vi.mocked(fetchAlerts).mockResolvedValue(alerts);
  let finish: (value: ReadinessResponse) => void = () => {};
  vi.mocked(fetchReadiness).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const view = await mount();
  await expect
    .element(view.getByText("Frontend heap pressure"))
    .toBeVisible();
  await expect
    .element(view.getByRole("status", { name: "Loading readiness audit" }))
    .toBeInTheDocument();
  expect(view.container.textContent).not.toContain("Refreshing");
  finish(readiness);
  await expect.element(view.getByText("Replica count")).toBeVisible();
  await expect
    .element(view.getByRole("status", { name: "Loading readiness audit" }))
    .not.toBeInTheDocument();
});

it("keeps the readiness audit when alert rules fail to load", async () => {
  vi.mocked(fetchAlerts).mockRejectedValue(new Error("offline"));
  vi.mocked(fetchReadiness).mockResolvedValue(readiness);
  const view = await mount();
  await expect
    .element(view.getByText("Could not load alert rules"))
    .toBeVisible();
  await expect.element(view.getByText("Replica count")).toBeVisible();
  vi.mocked(fetchAlerts).mockResolvedValue(alerts);
  await view.getByRole("button", { name: "Retry" }).click();
  await expect
    .element(view.getByText("Frontend heap pressure"))
    .toBeVisible();
});
