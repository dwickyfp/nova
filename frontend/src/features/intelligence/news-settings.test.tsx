import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api-client";
import { NewsSettings } from "./news-settings";
import { semanticViewsApi, type NewsDefaults, type SemanticView } from "./semantic-views-api";
import "@/styles/index.css";

const view: SemanticView = {
  id: "retail",
  name: "news_retail_sales",
  catalog_name: "default_catalog",
  database_name: "news_demo",
  schema_name: "",
  owner_name: "news_manager",
  active_version: 1,
  status: "ACTIVE",
  created_at: "2026-10-01",
  updated_at: "2026-10-01",
  news_enabled: false,
  news_config: null,
};
const defaults: NewsDefaults = {
  execution_role: "news_editor",
  metrics: ["revenue"],
  count_metric: "order_count",
  slice_dimensions: ["city", "channel"],
  time_dimension: "sale_date",
  available: {
    metrics: ["revenue", "order_count"],
    dimensions: ["city", "channel", "category"],
    time_dimensions: ["sale_date"],
  },
};

function mount(current: SemanticView, onChanged = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NewsSettings view={current} onChanged={onChanged} />
    </QueryClientProvider>,
  );
}

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
});

it("switches News on with the coverage a manager reviews", async () => {
  vi.spyOn(semanticViewsApi, "newsDefaults").mockResolvedValue(defaults);
  const save = vi.spyOn(semanticViewsApi, "setNews").mockResolvedValue({} as never);
  const onChanged = vi.fn();
  const screen = await mount(view, onChanged);

  await screen.getByRole("switch", { name: "Publish News from this view" }).click();
  await expect.element(screen.getByRole("checkbox", { name: "revenue" })).toBeChecked();
  await screen.getByRole("checkbox", { name: "category" }).click();
  await screen.getByRole("button", { name: "Save and switch on" }).click();

  await expect.poll(() => onChanged.mock.calls.length).toBe(1);
  expect(save).toHaveBeenCalledWith("retail", {
    enabled: true,
    config: {
      execution_role: "news_editor",
      metrics: ["revenue"],
      count_metric: "order_count",
      slice_dimensions: ["city", "channel", "category"],
      time_dimension: "sale_date",
      relative_threshold: 0.1,
      cadence_minutes: 60,
      narrative: "model",
    },
  });
});

it("shows the server's reason when the role has no scheduled account", async () => {
  vi.spyOn(semanticViewsApi, "newsDefaults").mockResolvedValue(defaults);
  vi.spyOn(semanticViewsApi, "setNews").mockRejectedValue(
    new ApiError(409, "Role 'news_editor' has no scheduled execution account."),
  );
  const screen = await mount(view);

  await screen.getByRole("switch", { name: "Publish News from this view" }).click();
  await screen.getByRole("button", { name: "Save and switch on" }).click();

  await expect
    .element(screen.getByRole("alert"))
    .toHaveTextContent("Role 'news_editor' has no scheduled execution account.");
});

it("switches News off at once and keeps the coverage summary until then", async () => {
  const save = vi.spyOn(semanticViewsApi, "setNews").mockResolvedValue({} as never);
  const screen = await mount({
    ...view,
    news_enabled: true,
    news_config: { ...defaults, cadence_minutes: 30 },
  });

  await expect.element(screen.getByText("revenue", { exact: true })).toBeVisible();
  await expect.element(screen.getByText(/within 30 minutes/)).toBeVisible();
  await screen.getByRole("switch", { name: "Publish News from this view" }).click();

  expect(save).toHaveBeenCalledWith("retail", { enabled: false });
});

it("shows only the state to a reader who cannot manage the view", async () => {
  const { news_config: _hidden, ...readerView } = { ...view, news_enabled: true };
  const screen = await mount(readerView);

  await expect.element(screen.getByText("On", { exact: true })).toBeVisible();
  expect(screen.getByRole("switch").query()).toBeNull();
});

it("asks for a published version before News can be switched on", async () => {
  const screen = await mount({ ...view, active_version: null });

  await expect
    .element(screen.getByText("Publish a version before switching News on."))
    .toBeVisible();
  await expect
    .element(screen.getByRole("switch", { name: "Publish News from this view" }))
    .toBeDisabled();
});
