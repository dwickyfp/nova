import { afterEach, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import { ContextPanel } from "./context-panel";
import "@/styles/index.css";

afterEach(async () => { await cleanup(); vi.restoreAllMocks(); });

it("uses the existing scoped traversal and keeps competing definitions visible", async () => {
  const source = { id: "n1", name: "Net revenue", kind: "metric", reference_id: "v1", state: "CONFLICTED", authority: "published_semantic_definition", source_kind: "published_semantic", authority_basis: { method: "authorized-published-definition-v1" }, validity: "current", freshness: "unknown", usage_count: 4, aliases: [], contradictions: ["n2"] };
  const get = vi.spyOn(api, "get").mockImplementation(async (url) => {
    if (url.startsWith("/intelligence/context/search")) return { items: [source], next_after: null } as never;
    if (url.startsWith("/intelligence/context/n1/graph")) return { nodes: [source, { ...source, id: "n2", reference_id: "v2", contradictions: ["n1"] }], edges: [], bounded: false, conflicts: [{ kind: "metric", term: "net revenue", node_ids: ["n1", "n2"], resolved: false }] } as never;
    throw new Error(`Unexpected request: ${url}`);
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ContextPanel /></QueryClientProvider>);
  await page.getByLabelText("Find a business concept").fill("Net revenue");
  await page.getByRole("button", { name: "Search context", exact: true }).click();
  await page.getByRole("button", { name: "Net revenue · metric", exact: true }).click();
  await expect.element(page.getByText("Unresolved definition conflicts", { exact: true })).toBeVisible();
  await expect.element(page.getByRole("region", { name: "Context authority and conflicts" })).toBeVisible();
  expect(get.mock.calls.filter(([url]) => url.includes("/graph"))).toHaveLength(1);
});

it("does not disclose inactive cached context from an older security scope", async () => {
  const client = new QueryClient();
  client.setQueryData(["context-graph", useAuthStore.getState().securityEpoch - 1, "old"], { nodes: [{ id: "old", name: "Private old context", source_kind: "published_semantic" }] });
  render(<QueryClientProvider client={client}><ContextPanel /></QueryClientProvider>);
  await expect.element(page.getByText("Private old context")).not.toBeInTheDocument();
});
