import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SemanticBiDatasets } from "./semantic-bi-datasets";
import { semanticViewsApi, type SemanticBiDataset } from "./semantic-views-api";
import "@/styles/index.css";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, search, ...props }: React.ComponentProps<"a"> & {
    to: string; search?: { path: string };
  }) => <a href={search ? `${to}?path=${encodeURIComponent(search.path)}` : to} {...props}>{children}</a>,
}));

const synced: SemanticBiDataset = {
  dataset: "visits",
  bi_dataset_id: 103,
  version: 3,
  status: "SYNCED",
  message: null,
  synced_at: "2026-10-30T09:50:00",
  bi_path: "/bi/explore/?datasource_type=table&datasource_id=103",
};

function mount(props: Partial<React.ComponentProps<typeof SemanticBiDatasets>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SemanticBiDatasets viewId="v1" published isPrivate={false} canSync {...props} />
    </QueryClientProvider>,
  );
}

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
});

it("syncs a published view to BI and opens its dataset inside Nova", async () => {
  vi.spyOn(semanticViewsApi, "biDatasets").mockResolvedValue({ datasets: [] });
  const sync = vi.spyOn(semanticViewsApi, "syncBi").mockResolvedValue({ datasets: [synced] });
  const screen = await mount();

  await expect.element(screen.getByText("Not in BI yet.")).toBeInTheDocument();
  await screen.getByRole("button", { name: "Sync to BI" }).click();

  expect(sync).toHaveBeenCalledWith("v1");
  await expect.element(screen.getByText("In BI", { exact: true })).toBeInTheDocument();
  await expect.element(screen.getByRole("link", { name: "Open in BI" })).toHaveAttribute(
    "href",
    `/analytics?path=${encodeURIComponent(synced.bi_path!)}`,
  );
  await expect.element(screen.getByRole("button", { name: "Sync again" })).toBeInTheDocument();
});

it("shows why a dataset was not synced", async () => {
  vi.spyOn(semanticViewsApi, "biDatasets").mockResolvedValue({
    datasets: [{ ...synced, status: "FAILED", bi_path: null, message: "BI refused the dataset" }],
  });
  const screen = await mount();

  await expect.element(screen.getByText("Not synced")).toBeInTheDocument();
  await expect.element(screen.getByText("BI refused the dataset")).toBeInTheDocument();
  await expect.element(screen.getByRole("link", { name: "Open in BI" })).not.toBeInTheDocument();
});

it("offers no sync for a private view or a reader", async () => {
  vi.spyOn(semanticViewsApi, "biDatasets").mockResolvedValue({ datasets: [] });
  const screen = await mount({ isPrivate: true });
  await expect.element(screen.getByText("Private views stay out of BI.")).toBeInTheDocument();
  await expect.element(screen.getByRole("button", { name: "Sync to BI" })).not.toBeInTheDocument();
  await cleanup();

  const reader = await mount({ canSync: false });
  await expect.element(reader.getByText("Not in BI yet.")).toBeInTheDocument();
  await expect.element(reader.getByRole("button", { name: "Sync to BI" })).not.toBeInTheDocument();
});
