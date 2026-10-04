import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { agentsApi } from "@/features/agents/api";
import { intelligenceApi } from "./lifecycle-api";
import { MonitorPanel } from "./monitor-panel";
import { semanticViewsApi } from "./semantic-views-api";
import "@/styles/index.css";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function mount() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <MonitorPanel onNews={vi.fn()} />
    </QueryClientProvider>,
  );
}

it.each(["UTC", "Europe/Berlin"])(
  "creates a monitor using configured %s without a Jakarta fallback",
  async (timezone) => {
    vi.spyOn(intelligenceApi, "executionTimezone").mockResolvedValue(timezone);
    vi.spyOn(intelligenceApi, "page").mockResolvedValue({
      items: [],
      next_after: null,
    });
    vi.spyOn(agentsApi, "list").mockResolvedValue({
      agents: [
        {
          agent_id: "finance",
          name: "Finance",
          semantic_view_ids: ["sales"],
        },
      ],
    } as Awaited<ReturnType<typeof agentsApi.list>>);
    vi.spyOn(semanticViewsApi, "get").mockResolvedValue({
      id: "sales",
      active_version: 1,
      versions: [
        {
          version: 1,
          fingerprint: "published",
          definition: {
            metrics: [{ name: "revenue" }, { name: "sample_count" }],
            datasets: [{ name: "orders", fields: [{ name: "date" }] }],
          },
        },
      ],
    } as Awaited<ReturnType<typeof semanticViewsApi.get>>);
    const post = vi
      .spyOn(api, "post")
      .mockResolvedValue({ id: "monitor", revision: 1 });
    const screen = await mount();
    await screen.getByText("Create a monitor", { exact: true }).click();
    await expect
      .element(screen.getByLabelText("Business timezone"))
      .toHaveValue(timezone);
    await screen.getByLabelText("Monitor name").fill("Revenue comparison");
    await screen.getByLabelText("Agent", { exact: true }).click();
    await screen.getByRole("option", { name: "Finance", exact: true }).click();
    await screen.getByLabelText("Monitored metric", { exact: true }).click();
    await screen.getByRole("option", { name: "revenue", exact: true }).click();
    await screen.getByLabelText("Sample count metric", { exact: true }).click();
    await screen
      .getByRole("option", { name: "sample_count", exact: true })
      .click();
    await screen.getByLabelText("Observation time", { exact: true }).click();
    await screen
      .getByRole("option", { name: "orders.date", exact: true })
      .click();
    await screen
      .getByRole("button", { name: "Save monitor", exact: true })
      .click();
    await expect.element(screen.getByText("Monitor saved.")).toBeVisible();
    expect(post).toHaveBeenCalledWith(
      "/intelligence/monitors",
      expect.objectContaining({
        configuration: expect.objectContaining({
          timezone,
          enabled: false,
          semantic: { view_id: "sales", version: 1, fingerprint: "published" },
        }),
        operation_id: expect.any(String),
      }),
    );
  },
);

it("does not invent a timezone when runtime configuration is unavailable", async () => {
  vi.spyOn(intelligenceApi, "executionTimezone").mockRejectedValue(
    new Error("Unavailable"),
  );
  vi.spyOn(intelligenceApi, "page").mockResolvedValue({
    items: [],
    next_after: null,
  });
  vi.spyOn(agentsApi, "list").mockResolvedValue({ agents: [], count: 0 });
  const screen = await mount();
  await screen.getByText("Create a monitor", { exact: true }).click();
  await expect
    .element(screen.getByLabelText("Business timezone"))
    .toHaveValue("");
  await expect
    .element(screen.getByRole("alert"))
    .toHaveTextContent("Enter the monitor timezone explicitly");
  await expect
    .element(screen.getByRole("button", { name: "Save monitor" }))
    .toBeDisabled();
});
