import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import { page } from "vitest/browser";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/index.css";
import { IntelligencePage } from "./intelligence-page";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, params, ...props }: React.ComponentProps<'a'> & {
    to: string; params?: { viewId: string };
  }) => <a href={params ? to.replace('$viewId', encodeURIComponent(params.viewId)) : to} {...props}>{children}</a>,
  useNavigate: () => vi.fn(),
}));
vi.mock("@/components/layout/header", () => ({
  Header: ({ children }: { children?: React.ReactNode }) => <header>{children}</header>,
}));

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ api: mocks }));

function renderIntelligence(section: "entities" | "search" | "semantic" | "features" = "search", semanticViewId?: string) {
  return render(
    <QueryClientProvider client={new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })}>
      <IntelligencePage section={section} semanticViewId={semanticViewId} />
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

  it.each([
    ["search", "AI Search", "/ai/search"],
    ["semantic", "Semantic Views", "/semantic-views"],
    ["features", "Feature Store", "/features/views"],
    ["entities", "Entities", "/entities"],
  ] as const)("shows only the %s page for its sidebar route", async (section, title, endpoint) => {
    const screen = await renderIntelligence(section);
    await expect.element(screen.getByRole("heading", { level: 1, name: title })).toBeVisible();
    expect(screen.getByRole("navigation", { name: "Intelligence sections" }).elements()).toHaveLength(0);
    await expect.poll(() => mocks.get.mock.calls.some(([path]) => path === endpoint)).toBe(true);
    const unrelated = ["/ai/search", "/semantic-views", "/features/views", "/entities"]
      .filter((path) => path !== endpoint && !(section === "features" && path === "/entities"));
    expect(mocks.get.mock.calls.some(([path]) => unrelated.includes(path))).toBe(false);
  });

  it("opens a Semantic View on its own detail route", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (path: string) => path === "/semantic-views"
      ? [{ id: "sv1", name: "sales_360", database_name: "NOVA_DEMO",
        active_version: 1, status: "ACTIVE" }]
      : defaultGet?.(path));
    const screen = await renderIntelligence("semantic");
    const viewLink = screen.getByRole("link", { name: /sales_360/ });
    await expect.element(viewLink).toHaveAttribute("href", "/semantic-views/sv1");
    expect(screen.getByRole("region", { name: "Semantic View details" }).elements()).toHaveLength(0);
    expect(mocks.get.mock.calls.some(([path]) => path === "/semantic-views/sv1")).toBe(false);
  });

  it("shows a recoverable error instead of view actions when a detail URL cannot load", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (path: string) => path === "/semantic-views/missing"
      ? Promise.reject(new Error("Missing view")) : defaultGet?.(path));
    const screen = await renderIntelligence("semantic", "missing");
    await expect.element(screen.getByRole("alert")).toHaveTextContent("Could not load this Semantic View.");
    await expect.element(screen.getByRole("link", { name: "All Semantic Views" })).toHaveAttribute("href", "/semantic-views");
    expect(screen.getByText("Manage this view").elements()).toHaveLength(0);
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

  it("explains an index that is still building before search", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (path: string) => path === "/ai/search"
      ? [{ name: "pending", source_relation: "SALES.docs", model_alias: null,
        active_version: null, status: "BUILDING" }]
      : defaultGet?.(path));
    const screen = await renderIntelligence();
    await userEvent.click(screen.getByRole("button", { name: /pending/ }));
    await expect.element(screen.getByText(/no active version yet/)).toBeVisible();
    await userEvent.fill(screen.getByRole("textbox", { name: "Search query" }), "shoe");
    await expect.element(screen.getByRole("button", { name: "Search", exact: true })).toBeDisabled();
  });

  it("creates a lexical index from labeled fields", async () => {
    const screen = await renderIntelligence();
    await userEvent.click(screen.getByText("Create a search index"));
    await userEvent.fill(screen.getByRole("textbox", { name: "Index name" }), "catalog");
    await userEvent.click(screen.getByRole("button", { name: "Source relation database" }));
    await userEvent.click(screen.getByRole("button", { name: "NOVA_SYSTEM", exact: true }));
    await userEvent.click(screen.getByRole("button", { name: "Source relation relation" }));
    await userEvent.click(screen.getByRole("button", { name: "catalog", exact: true }));
    await userEvent.click(screen.getByRole("button", { name: "Key columns" }));
    await userEvent.click(screen.getByText("id", { exact: true }));
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Search text columns" }));
    await userEvent.click(screen.getByText("body", { exact: true }).last());
    await userEvent.keyboard("{Escape}");
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
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Target column" }));
    await userEvent.click(screen.getByRole("button", { name: "churn" }).last());
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
    const screen = await renderIntelligence("semantic", "sv1");
    await expect.element(screen.getByText("Total revenue?: result_changed")).toBeVisible();
    await userEvent.click(screen.getByRole("checkbox", {
      name: "Acknowledge verified-query changes before publishing",
    }));
    await userEvent.click(screen.getByRole("button", { name: "Publish", exact: true }));
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
    const screen = await renderIntelligence("semantic", "sv1");
    await userEvent.click(screen.getByRole("button", { name: "Use as draft" }));
    await expect.element(screen.getByRole("textbox", { name: /New version definition/ })).toBeVisible();
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
    const screen = await renderIntelligence("semantic", "sv1");
    await userEvent.click(screen.getByRole("button", { name: "Metrics" }));
    await userEvent.click(screen.getByText("total_revenue", { exact: true }).last());
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Dimensions" }));
    await userEvent.click(screen.getByText("customers.country", { exact: true }));
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Named filters" }));
    await userEvent.click(screen.getByText("completed_orders", { exact: true }).last());
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Run query" }));
    await expect.poll(() => mocks.post.mock.calls.length).toBe(1);
    expect(mocks.post).toHaveBeenCalledWith("/semantic-views/sv1/query", {
      metrics: ["total_revenue"], dimensions: ["customers.country"],
      named_filters: ["completed_orders"], version: null,
    });
  });

  it("inspects a View definition, quality report, and question preview", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/semantic-views") return [{
        id: "sv1", name: "sales_360", catalog_name: "default_catalog",
        database_name: "NOVA_DEMO", schema_name: "", owner_name: "nova_admin",
        active_version: 1, status: "ACTIVE", created_at: "2026-09-24T00:00:00Z",
        updated_at: "2026-09-24T00:00:00Z",
      }];
      if (path === "/semantic-views/sv1") return {
        id: "sv1", name: "sales_360", catalog_name: "default_catalog",
        database_name: "NOVA_DEMO", schema_name: "", owner_name: "nova_admin",
        active_version: 1, status: "ACTIVE", created_at: "2026-09-24T00:00:00Z",
        updated_at: "2026-09-24T00:00:00Z", versions: [{
          version: 1, status: "ACTIVE", fingerprint: "fingerprint-1", validation: null,
          definition: {
            name: "sales_360", description: "Sales measures", hierarchies: { location: ["country", "city"] },
            datasets: [{ name: "orders", source: "NOVA_DEMO.orders", grain: { keys: ["id"] },
              unique_keys: [["id", "region"]],
              fields: [{ name: "region", datatype: "String", expression: "region", dimension: {},
                sample_values: ["West"] }] }],
            metrics: [{ name: "revenue", expression: "SUM(orders.amount)",
              dependencies: ["gross_revenue"] }],
            relationships: [{ name: "order_customer", from: "orders", to: "customers",
              from_columns: ["customer_id"], to_columns: ["id"] }],
            named_filters: [{ name: "completed", expression: "status = 'complete'" }],
            verified_queries: [{ verified_query_id: "q1", question: "Revenue?",
              semantic_plan: {}, verified_sql: "SELECT SUM(amount) FROM orders" }],
          },
        }],
      };
      if (path === "/semantic-views/sv1/versions/1/quality") return {
        view_id: "sv1", version: 1, model_fingerprint: "fingerprint-1", valid: true,
        errors: [], findings: [{ code: "MISSING_SYNONYM", severity: "warning",
          message: "Add a synonym", object_name: "revenue" }],
        quality: { dataset_count: 1 }, verified_queries: [], quality_lab: null,
      };
      return defaultGet?.(path);
    });
    mocks.post.mockImplementation(async (path: string) => {
      if (path === "/semantic-views/sv1/versions/1/preview") return {
        view_id: "sv1", version: 1, model_fingerprint: "fingerprint-1",
        semantic_plan: { metrics: ["revenue"] }, generated_sql: "SELECT SUM(amount) FROM orders",
        confidence: { level: "high", score: 0.9 }, relationship_path: ["orders"], warnings: [],
      };
      return {};
    });

    const screen = await renderIntelligence("semantic", "sv1");
    await screen.getByRole("tab", { name: "Definition" }).click();
    await expect.element(screen.getByText("NOVA_DEMO.orders", { exact: true })).toBeVisible();
    await expect.element(screen.getByText("Unique keys: id + region", { exact: true })).toBeVisible();
    await expect.element(screen.getByText("location", { exact: true })).toBeVisible();
    await expect.element(screen.getByText("Derived from: gross_revenue", { exact: true })).toBeVisible();
    await expect.element(screen.getByText("order_customer", { exact: true })).toBeVisible();
    await screen.getByRole("tab", { name: "Verified questions" }).click();
    await expect.element(screen.getByText("Revenue?", { exact: true })).toBeVisible();
    await screen.getByRole("tab", { name: "Quality" }).click();
    await expect.element(screen.getByText("Add a synonym")).toBeVisible();
    await screen.getByRole("tab", { name: "Question preview" }).click();
    await screen.getByRole("textbox", { name: "Business question" }).fill("Revenue?");
    await screen.getByRole("button", { name: "Preview question" }).click();
    await expect.element(screen.getByText("SELECT SUM(amount) FROM orders", { exact: true })).toBeVisible();
    expect(mocks.post).toHaveBeenCalledWith("/semantic-views/sv1/versions/1/preview", { question: "Revenue?" });
  });

  it("keeps Semantic View details within a narrow viewport", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/semantic-views") return [{ id: "sv1", name: "sales_360",
        database_name: "NOVA_DEMO", active_version: 1, status: "ACTIVE" }];
      if (path === "/semantic-views/sv1") return { id: "sv1", name: "sales_360",
        database_name: "NOVA_DEMO", active_version: 1, status: "ACTIVE",
        versions: [{ version: 1, status: "ACTIVE", definition: {
          datasets: [{ name: "orders", source: "NOVA_DEMO.orders",
            fields: [{ name: "long_field_name", datatype: "String",
              expression: "a_long_expression_that_requires_horizontal_table_scrolling" }] }],
          metrics: [], relationships: [], named_filters: [],
        }, validation: null }],
      };
      return defaultGet?.(path);
    });
    await page.viewport(320, 640);
    try {
      const screen = await renderIntelligence("semantic", "sv1");
      await screen.getByRole("tab", { name: "Definition" }).click();
      await expect.element(screen.getByText("NOVA_DEMO.orders", { exact: true })).toBeVisible();
      const surface = screen.getByRole("region", { name: "Semantic View details" }).element();
      expect(surface.getBoundingClientRect().right).toBeLessThanOrEqual(320);
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
    } finally {
      await page.viewport(1280, 720);
    }
  });

  it("keeps an unsupported imported View readable while requiring a new supported version", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    const original = { version: "1.0", name: "legacy_sales",
      semantic_model: [{ name: "legacy_orders" }], datasets: { legacy_orders: { table: "orders" } },
      metrics: { revenue: { sql: "SUM(amount)" } } };
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/semantic-views") return [{ id: "legacy-1", name: "legacy_sales",
        catalog_name: "default_catalog", database_name: "NOVA_SYSTEM", schema_name: "",
        owner_name: "owner", visibility: "PRIVATE", active_version: null, status: "DRAFT" }];
      if (path === "/semantic-views/legacy-1") return { id: "legacy-1", name: "legacy_sales",
        catalog_name: "default_catalog", database_name: "NOVA_SYSTEM", schema_name: "",
        owner_name: "owner", visibility: "PRIVATE", active_version: null, status: "DRAFT",
        versions: [{ version: 1, status: "DRAFT", definition: {
          ...original, verified_queries: [{ question: "Old revenue?", semantic_plan: {},
            verified_sql: "SELECT SUM(amount) FROM orders" }],
        }, validation: { valid: false, errors: ["Unsupported legacy Ossie version 1.0"],
          warnings: [], migration: { source: "CONFIG_SEMANTIC_MODELS", legacy_version: "1.0",
            raw_definition_preserved: true, raw_definition: original } } }] };
      return defaultGet?.(path);
    });
    const screen = await renderIntelligence("semantic", "legacy-1");
    await screen.getByRole("tab", { name: "Definition" }).click();
    await expect.element(screen.getByText(/Legacy format: add a supported Ossie 0.1.1 version/).first()).toBeVisible();
    await expect.element(screen.getByText("Original imported definition")).toBeVisible();
    await expect.element(screen.getByText(/scope pending review/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Validate" }).elements()).toHaveLength(0);
    await screen.getByRole("tab", { name: "Verified questions" }).click();
    await expect.element(screen.getByText("Old revenue?", { exact: true })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Copy original to editor" }));
    await expect.element(screen.getByRole("textbox", { name: /New version definition/ })).toBeVisible();
    expect((screen.getByRole("textbox", { name: /New version definition/ }).element() as HTMLTextAreaElement).value)
      .toContain('"semantic_model"');
  });

  it("explains the empty Semantic View catalog", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    let finishList: (views: unknown[]) => void = () => {};
    const pendingList = new Promise<unknown[]>((resolve) => { finishList = resolve; });
    mocks.get.mockImplementation(async (path: string) =>
      path === "/semantic-views" ? pendingList : defaultGet?.(path));
    const screen = await renderIntelligence("semantic");
    await expect.element(screen.getByText("Loading Semantic Views")).toBeVisible();
    finishList([]);
    await expect.element(screen.getByText(/No Semantic Views yet/)).toBeVisible();
    await expect.element(screen.getByRole("link", { name: "Build visually" })).toHaveAttribute("href", "/semantic-views/builder");
  });

  it("shows a retry action when View quality cannot load", async () => {
    const defaultGet = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/semantic-views") return [{ id: "sv1", name: "sales_360",
        database_name: "NOVA_DEMO", active_version: 1, status: "ACTIVE" }];
      if (path === "/semantic-views/sv1") return { id: "sv1", name: "sales_360",
        active_version: 1, status: "ACTIVE", versions: [{ version: 1, status: "ACTIVE",
          definition: { datasets: [], metrics: [] }, validation: null }] };
      if (path === "/semantic-views/sv1/versions/1/quality") throw new Error("offline");
      return defaultGet?.(path);
    });
    const screen = await renderIntelligence("semantic", "sv1");
    await screen.getByRole("tab", { name: "Quality" }).click();
    await expect.element(screen.getByText("Could not load quality report")).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });
});
