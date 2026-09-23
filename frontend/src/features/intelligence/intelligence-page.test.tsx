import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { IntelligencePage } from "./intelligence-page";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: mocks }));

function renderIntelligence(initialTab: "entities" | "search" | "semantic" | "features" = "search") {
  return render(
    <QueryClientProvider client={new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })}>
      <IntelligencePage initialTab={initialTab} />
    </QueryClientProvider>,
  );
}

describe("Nova Intelligence", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/explorer/catalogs") return { catalogs: [{
        name: "default_catalog", type: "internal", comment: null,
        databases: ["NOVA_SYSTEM"],
      }] };
      if (path === "/explorer/databases/NOVA_SYSTEM") return {
        database: "NOVA_SYSTEM", tables: [
          { name: "catalog" }, { name: "labels" },
        ], views: [],
      };
      if (path === "/explorer/databases/NOVA_SYSTEM/tables/catalog") return {
        database: "NOVA_SYSTEM", table: "catalog",
        columns: [{ name: "id" }, { name: "body" }],
      };
      if (path === "/explorer/databases/NOVA_SYSTEM/tables/labels") return {
        database: "NOVA_SYSTEM", table: "labels",
        columns: [{ name: "customer_id" }, { name: "label_ts" }, { name: "churn" }],
      };
      if (path === "/ai/providers") return { providers: [] };
      if (path === "/ai/search") return [{
        name: "docs", source_relation: "NOVA_SYSTEM.docs", model_alias: "nova.embedding.default",
        active_version: 1, status: "ACTIVE",
      }];
      if (path === "/ai/search/docs") return {
        versions: [{ version: 1, build_status: "ACTIVE" }],
      };
      return [];
    });
    mocks.post.mockResolvedValue({
      version: 1, mode: "HYBRID",
      hits: [{ source_key: "[1]", content: "running shoe", score: 0.1 }],
    });
  });

  it("queries a selected search index and displays authorized hits", async () => {
    const screen = await renderIntelligence();
    await expect.element(screen.getByText("NOVA_SYSTEM.docs · ACTIVE · v1")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /docs/ }));
    await userEvent.fill(screen.getByRole("textbox", { name: "Search query" }), "shoe");
    await userEvent.click(screen.getByRole("button", { name: "Search", exact: true }));
    await expect.element(screen.getByText("running shoe")).toBeVisible();
    expect(mocks.post).toHaveBeenCalledWith("/ai/search/docs/query", {
      query: "shoe", mode: "HYBRID",
    });
  });

  it("creates a lexical index from labeled fields", async () => {
    const screen = await renderIntelligence();
    await userEvent.fill(screen.getByRole("textbox", { name: "Index name" }), "catalog");
    await userEvent.click(screen.getByRole("button", { name: "Source relation database" }));
    await userEvent.click(screen.getByRole("button", { name: "NOVA_SYSTEM", exact: true }));
    await userEvent.click(screen.getByRole("button", { name: "Source relation relation" }));
    await userEvent.click(screen.getByRole("button", { name: "catalog", exact: true }));
    await userEvent.click(screen.getByRole("button", { name: "Key columns" }));
    await userEvent.click(screen.getByText("id", { exact: true }));
    await userEvent.click(screen.getByRole("button", { name: "Search text columns" }));
    await userEvent.click(screen.getByText("body", { exact: true }));
    await userEvent.click(screen.getByRole("button", { name: "Create search index" }));
    await expect.poll(() => mocks.post.mock.calls.length).toBe(1);
    expect(mocks.post).toHaveBeenCalledWith("/ai/search", expect.objectContaining({
      name: "catalog", source_relation: "NOVA_SYSTEM.catalog", key_columns: ["id"],
      content_columns: ["body"], model_alias: null,
    }));
  });

  it("trains from a selected Feature Group with its entity keys", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/entities") return [{ id: "e1", name: "customer",
        relation: "NOVA_SYSTEM.customers", key_columns: ["customer_id"] }];
      if (path === "/features/groups") return [{
        name: "customer_features", entity_id: "e1", active_version: 2, status: "ACTIVE",
      }];
      return defaultGet?.(path);
    });
    const screen = await renderIntelligence("features");
    await expect.element(screen.getByText("ACTIVE · v2")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /customer_features/ }));
    await userEvent.click(screen.getByRole("button", { name: "Label relation database" }));
    await userEvent.click(screen.getByRole("button", { name: "NOVA_SYSTEM" }));
    await userEvent.click(screen.getByRole("button", { name: "Label relation relation" }));
    await userEvent.click(screen.getByRole("button", { name: "labels" }));
    await userEvent.click(screen.getByRole("button", { name: "Label timestamp column" }));
    await userEvent.click(screen.getByRole("button", { name: "label_ts" }));
    await userEvent.click(screen.getByRole("button", { name: "Target column" }));
    await userEvent.click(screen.getByRole("button", { name: "churn" }));
    await userEvent.fill(screen.getByRole("textbox", { name: "Model name" }), "churn_model");
    await userEvent.click(screen.getByRole("button", { name: "Train model" }));
    await expect.poll(() => mocks.post.mock.calls.length).toBe(1);
    expect(mocks.post).toHaveBeenCalledWith("/features/groups/customer_features/train",
      expect.objectContaining({
        entity_keys: ["customer_id"], model_name: "churn_model", target_column: "churn",
      }));
  });

  it("looks up a Feature Group using every registered entity key", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/entities") return [{ id: "e2", name: "account",
        relation: "NOVA_SYSTEM.accounts", key_columns: ["tenant_id", "account_id"] }];
      if (path === "/features/groups") return [{
        name: "account_features", entity_id: "e2", active_version: 1, status: "ACTIVE",
      }];
      return defaultGet?.(path);
    });
    const screen = await renderIntelligence("features");
    await userEvent.click(screen.getByRole("button", { name: /account_features/ }));
    await userEvent.fill(screen.getByRole("textbox", { name: "tenant_id" }), "north");
    await userEvent.fill(screen.getByRole("textbox", { name: "account_id" }), "42");
    await userEvent.click(screen.getByRole("button", { name: "Lookup" }));
    await expect.poll(() => mocks.post.mock.calls.length).toBe(1);
    expect(mocks.post).toHaveBeenCalledWith("/features/groups/account_features/lookup", {
      entity_key: { tenant_id: "north", account_id: "42" },
    });
  });

  it("shows Semantic View regression evidence before an acknowledged publish", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/semantic-views") return [{
        id: "sv1", name: "sales_360", database_name: "NOVA_DEMO",
        active_version: 1, status: "ACTIVE",
      }];
      if (path === "/semantic-views/sv1") return {
        id: "sv1", name: "sales_360", database_name: "NOVA_DEMO",
        active_version: 1, status: "ACTIVE", versions: [
          { version: 2, status: "VALIDATED", definition: {
            metrics: [{ name: "total_revenue" }], datasets: [],
          }, validation: { valid: true, errors: [], warnings: [], regression: {
            changed: 1, cases: [{ verified_query_id: "revenue",
              question: "Total revenue?", status: "result_changed" }],
          } } },
        ],
      };
      return defaultGet?.(path);
    });
    const screen = await renderIntelligence("semantic");
    await userEvent.click(screen.getByRole("button", { name: /sales_360/ }));
    await expect.element(screen.getByText("Total revenue?: result_changed")).toBeVisible();
    await userEvent.click(screen.getByRole("checkbox", {
      name: "Acknowledge verified-query changes before publishing",
    }));
    await userEvent.click(screen.getByRole("button", { name: "Publish" }));
    await expect.poll(() => mocks.post.mock.calls.length).toBe(1);
    expect(mocks.post).toHaveBeenCalledWith(
      "/semantic-views/sv1/versions/2/publish",
      { acknowledge_regressions: true },
    );
  });

  it("opens a saved Semantic View definition as a valid new Ossie draft", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/semantic-views") return [{
        id: "sv1", name: "sales_360", database_name: "NOVA_DEMO",
        active_version: 1, status: "ACTIVE",
      }];
      if (path === "/semantic-views/sv1") return {
        active_version: 1, versions: [{ version: 1, status: "ACTIVE", validation: null,
          definition: { name: "sales_360", datasets: [{ name: "orders",
            source: "NOVA_DEMO.orders", fields: [{ name: "amount", expression: "amount" }] }],
          metrics: [{ name: "total", expression: "SUM(orders.amount)" }] },
        }],
      };
      return defaultGet?.(path);
    });
    const screen = await renderIntelligence("semantic");
    await userEvent.click(screen.getByRole("button", { name: /sales_360/ }));
    await userEvent.click(screen.getByRole("button", { name: "Use as draft" }));
    await userEvent.click(screen.getByRole("button", { name: "Add version" }));
    await expect.poll(() => mocks.post.mock.calls.length).toBe(1);
    const body = mocks.post.mock.calls[0][1] as { definition: string };
    const parsed = JSON.parse(body.definition);
    expect(parsed.datasets[0].fields[0].expression.dialects[0].expression).toBe("amount");
    expect(parsed.metrics[0].expression.dialects[0].expression).toBe("SUM(orders.amount)");
  });

  it("queries a Semantic View with selected metrics, dimensions, and named filters", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/semantic-views") return [{
        id: "sv1", name: "sales_360", database_name: "NOVA_DEMO",
        active_version: 1, status: "ACTIVE",
      }];
      if (path === "/semantic-views/sv1") return {
        active_version: 1, versions: [{ version: 1, status: "ACTIVE", validation: null,
          definition: { metrics: [{ name: "total_revenue" }],
            named_filters: [{ name: "completed_orders" }],
            datasets: [{ name: "customers", fields: [
              { name: "country", kind: "dimension" },
            ] }] },
        }],
      };
      return defaultGet?.(path);
    });
    const screen = await renderIntelligence("semantic");
    await userEvent.click(screen.getByRole("button", { name: /sales_360/ }));
    await userEvent.click(screen.getByRole("button", { name: "Metrics" }));
    await userEvent.click(screen.getByText("total_revenue", { exact: true }));
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Dimensions" }));
    await userEvent.click(screen.getByText("customers.country", { exact: true }));
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Named filters" }));
    await userEvent.click(screen.getByText("completed_orders", { exact: true }));
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Run query" }));
    await expect.poll(() => mocks.post.mock.calls.length).toBe(1);
    expect(mocks.post).toHaveBeenCalledWith("/semantic-views/sv1/query", {
      metrics: ["total_revenue"], dimensions: ["customers.country"],
      named_filters: ["completed_orders"], version: null,
    });
  });
});
