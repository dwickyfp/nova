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

it("resolves the selected historical metric exactly and binds authority to that graph", async () => {
  const semantic = { view_id: "sales", version: 2, fingerprint: "published-v2" };
  const graph = {
    nodes: [{ id: "selected", name: "Selected revenue", kind: "metric", reference_id: "sales:2:revenue", state: "VERIFIED", semantic, source_kind: "published_semantic", validity: "historical", authority_basis: { version: 2 }, contradictions: [] }],
    edges: [], bounded: true, conflicts: [],
  };
  const post = vi.spyOn(api, "post").mockResolvedValue({ status: "resolved", selected_metric: "revenue", node_id: "selected", selected_node: graph.nodes[0], semantic, graph } as never);
  const get = vi.spyOn(api, "get").mockRejectedValue(new Error("Unexpected search"));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["context-graph", useAuthStore.getState().securityEpoch, "unrelated"], { nodes: [{ id: "unrelated", name: "Unrelated authority", source_kind: "published_semantic" }] });
  render(<QueryClientProvider client={client}><ContextPanel semantic={semantic} metric="revenue" /></QueryClientProvider>);
  await expect.element(page.getByRole("region", { name: "Context authority and conflicts" })).toBeVisible();
  await expect.element(page.getByText("Unrelated authority")).not.toBeInTheDocument();
  await expect.element(page.getByText("historical", { exact: true })).toBeVisible();
  expect(post).toHaveBeenCalledWith("/intelligence/context/resolve-metric", { semantic, term: "revenue", exact: true, include_context: true });
  expect(get).not.toHaveBeenCalled();
});

it("clears prior selection authority when the new exact metric is denied", async () => {
  const semantic = { view_id: "sales", version: 2, fingerprint: "published-v2" };
  const post = vi.spyOn(api, "post").mockImplementation(async (_url, body) => {
    if ((body as { term: string }).term === "denied") throw new Error("Permission denied");
    return { status: "resolved", node_id: "selected", semantic, graph: { nodes: [{ id: "selected", name: "Earlier selected metric", kind: "metric", reference_id: "metric", state: "VERIFIED", source_kind: "published_semantic" }], edges: [], bounded: true } } as never;
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = await render(<QueryClientProvider client={client}><ContextPanel semantic={semantic} metric="revenue" /></QueryClientProvider>);
  await expect.element(page.getByRole("region", { name: "Context authority and conflicts" })).toBeVisible();
  await view.rerender(<QueryClientProvider client={client}><ContextPanel semantic={semantic} metric="denied" /></QueryClientProvider>);
  await expect.element(page.getByRole("alert")).toBeVisible();
  await expect.element(page.getByText("Earlier selected metric", { exact: true })).not.toBeInTheDocument();
  expect(post).toHaveBeenCalledTimes(2);
});

it.each([false, true])("keeps concept links readable on the panel surface (dark=%s)", async (dark) => {
  const prior = document.documentElement.classList.contains("dark");
  document.documentElement.classList.toggle("dark", dark);
  const semantic = { view_id: "sales", version: 2, fingerprint: "published-v2" };
  vi.spyOn(api, "post").mockResolvedValue({ status: "resolved", node_id: "selected", semantic, graph: { nodes: [{ id: "selected", name: "Revenue concept", kind: "metric", reference_id: "metric", state: "VERIFIED", source_kind: "published_semantic" }], edges: [], bounded: true } } as never);
  try {
    await render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ContextPanel semantic={semantic} metric="revenue" /></QueryClientProvider>);
    const link = page.getByRole("button", { name: "Revenue concept", exact: true });
    await expect.element(link).toBeVisible();
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const colors = canvas.getContext("2d")!;
    const luminance = (color: string) => {
      colors.clearRect(0, 0, 1, 1);
      colors.fillStyle = color;
      colors.fillRect(0, 0, 1, 1);
      const [red, green, blue] = [...colors.getImageData(0, 0, 1, 1).data].slice(0, 3).map((value) => {
        const channel = value / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
    };
    await vi.waitFor(() => {
      expect(link.element().getAnimations().filter((animation) => animation.pending || animation.playState === "running")).toHaveLength(0);
      const foreground = luminance(getComputedStyle(link.element()).color);
      const background = luminance(getComputedStyle(document.body).backgroundColor);
      expect((Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05)).toBeGreaterThanOrEqual(4.5);
    });
    link.element().focus();
    await expect.element(link).toHaveFocus();
  } finally {
    document.documentElement.classList.toggle("dark", prior);
  }
});
