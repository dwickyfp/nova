import { afterEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/index.css";
import type { SemanticModel } from "./api";
import { SemanticInspector } from "./semantic-inspector";

const MODEL: SemanticModel = {
  semantic_model_id: "sales/model",
  owner_name: "nova_admin",
  name: "Sales",
  description: "Governed sales metrics",
  database_name: "NOVA_DEMO",
  schema_name: null,
  ossie_version: "0.1.1",
  definition: {},
  source_file_id: null,
  created_at: "2026-09-22T00:00:00Z",
  updated_at: "2026-09-22T00:00:00Z",
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SemanticInspector", () => {
  it("shows semantic plan, relationship path, SQL, quality, and VQR actions", async () => {
    await page.viewport(1280, 800);
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/lint")) {
          return json({
            semantic_model_id: "sales/model",
            model_fingerprint: "fingerprint-1",
            valid: true,
            errors: [],
            findings: [
              {
                code: "missing_synonym",
                severity: "warning",
                message: "Add synonyms.",
              },
            ],
            quality: { datasets_with_grain: 1, verified_query_count: 0 },
          });
        }
        if (url.endsWith("/verified-queries") && init?.method === "POST") {
          return json({ verified_query_id: "vq-1" }, 201);
        }
        if (url.endsWith("/verified-queries"))
          return json({ queries: [], count: 0 });
        if (url.endsWith("/preview")) {
          return json({
            semantic_model_id: "sales/model",
            model_fingerprint: "fingerprint-1",
            semantic_plan: {
              metrics: ["total_revenue"],
              dimensions: ["region"],
            },
            generated_sql:
              "SELECT region, SUM(amount) AS total_revenue FROM orders GROUP BY region",
            confidence: { score: 0.96, level: "high", unresolved: [] },
            relationship_path: ["orders", "customers", "regions"],
            warnings: [],
          });
        }
        return json({});
      });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const screen = await render(
      <QueryClientProvider client={client}>
        <SemanticInspector model={MODEL} onOpenChange={() => {}} />
      </QueryClientProvider>,
    );

    await screen
      .getByRole("textbox", { name: "Semantic question" })
      .fill("Revenue by region");
    await screen.getByRole("button", { name: "Preview" }).click();
    await expect.element(screen.getByText("96%")).toBeVisible();
    await expect.element(screen.getByText("customers")).toBeVisible();
    await expect.element(screen.getByText(/SUM\(amount\)/)).toBeVisible();
    await screen.getByRole("button", { name: "Save verified query" }).click();
    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) => {
          return (
            String(url).endsWith("/verified-queries") && init?.method === "POST"
          );
        }),
      ).toBe(true);
    });

    await screen.getByRole("tab", { name: "Quality" }).click();
    await expect.element(screen.getByText("Compiler-ready")).toBeVisible();
    await expect.element(screen.getByText("missing_synonym")).toBeVisible();
  });
});

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}
