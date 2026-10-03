import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import type { AgentMemory } from "@/features/agents/api";
import { ContextInspector } from "@/features/intelligence/context-inspector";
import { KnowledgePanel } from "./knowledge-panel";

const semantic = { view_id: "view", version: 1, fingerprint: "definition" };
const memory: AgentMemory = {
  memory_id: "memory",
  user_name: "analyst",
  agent_id: "agent",
  role_name: "FINANCE",
  fact_key: "revenue",
  fact: "Revenue uses completed orders.",
  source_quote: "Revenue uses completed orders.",
  source_thread_id: "thread",
  created_at: "2026-03-01",
  updated_at: "2026-03-01",
};

function wrapper(client: QueryClient, child: React.ReactNode) {
  return <QueryClientProvider client={client}>{child}</QueryClientProvider>;
}

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
});

it("opens pinned knowledge context and removes it after an access change", async () => {
  let denied = false;
  vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (denied) throw new ApiError(403, "Denied");
    if (path.endsWith("/revisions"))
      return {
        items: [
          {
            memory_id: memory.memory_id,
            revision: 1,
            fact: memory.fact,
            state: "HYPOTHESIS",
            visibility: "PRIVATE",
            authority: "user_statement",
            alternatives: [],
            evidence_ids: [],
            review_note: "",
            needs_revalidation: false,
          },
        ],
      };
    if (path.endsWith("/evidence")) return { items: [] };
    if (path.includes("/graph?"))
      return {
        nodes: [
          {
            id: "node",
            name: "Private revenue context",
            kind: "rule",
            reference_id: "memory",
            state: "HYPOTHESIS",
          },
        ],
        edges: [],
        bounded: false,
      };
    return { semantic_view_ids: [] };
  });
  const post = vi.spyOn(api, "post").mockResolvedValue({ root_id: "node" });
  const screen = await render(
    wrapper(
      new QueryClient(),
      <KnowledgePanel agentId="agent" memory={memory} />,
    ),
  );
  await screen
    .getByRole("button", { name: "Inspect knowledge context" })
    .click();
  await expect
    .element(screen.getByRole("button", { name: "Private revenue context" }))
    .toBeVisible();
  expect(post).toHaveBeenCalledWith("/agents/agent/memories/memory/context");
  denied = true;
  useAuthStore.setState((state) => ({
    securityEpoch: state.securityEpoch + 1,
  }));
  await expect
    .element(
      screen.getByText("Knowledge is unavailable under your current access."),
    )
    .toBeVisible();
  await expect
    .element(screen.getByRole("button", { name: "Private revenue context" }))
    .not.toBeInTheDocument();
});

it("changes the selected context node even when the semantic version is unchanged", async () => {
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => ({
    nodes: [
      {
        id: path,
        name: path.includes("/first/") ? "First decision" : "Second decision",
        kind: "decision",
        reference_id: path,
        state: "INFERRED",
      },
    ],
    edges: [],
    bounded: false,
  }));
  const client = new QueryClient();
  const component = (node: string) =>
    wrapper(
      client,
      <ContextInspector semantic={semantic} initialNodeId={node} />,
    );
  const screen = await render(component("first"));
  await expect
    .element(screen.getByRole("button", { name: "First decision" }))
    .toBeVisible();
  await screen.rerender(component("second"));
  await expect
    .element(screen.getByRole("button", { name: "Second decision" }))
    .toBeVisible();
  await expect
    .element(screen.getByRole("button", { name: "First decision" }))
    .not.toBeInTheDocument();
  expect(get).toHaveBeenCalledWith(
    "/intelligence/context/second/graph?depth=2&limit=50",
  );
});

it.each([
  ["Published filter", "filter", "healthy", "Verify published definition"],
  [
    "Published dimension",
    "dimension",
    "orders.city",
    "Verify published definition",
  ],
  ["Business heuristic", "heuristic", undefined, "Verify reviewed heuristic"],
])(
  "reviews %s against the exact published version",
  async (label, kind, name, action) => {
    vi.spyOn(api, "get").mockImplementation(async (path) => {
      if (path.endsWith("/revisions"))
        return {
          items: [
            {
              memory_id: memory.memory_id,
              revision: 3,
              fact: memory.fact,
              state: "HYPOTHESIS",
              visibility: "PRIVATE",
              authority: "user_statement",
              alternatives: [],
              evidence_ids: ["source"],
              review_note: "",
              needs_revalidation: false,
            },
          ],
        };
      if (path.endsWith("/evidence")) return { items: [] };
      if (path === "/semantic-views/view")
        return {
          id: "view",
          name: "Sales",
          active_version: 7,
          versions: [
            {
              version: 7,
              fingerprint: "published",
              definition: {
                metrics: [{ name: "revenue" }],
                named_filters: [{ name: "healthy" }],
                datasets: [
                  { name: "orders", fields: [{ name: "city", dimension: {} }] },
                ],
              },
            },
          ],
        };
      return { semantic_view_ids: ["view"] };
    });
    const post = vi.spyOn(api, "post").mockResolvedValue({});
    const screen = await render(
      wrapper(
        new QueryClient(),
        <KnowledgePanel agentId="agent" memory={memory} />,
      ),
    );
    await screen.getByRole("combobox", { name: "Knowledge type" }).click();
    await screen.getByRole("option", { name: label, exact: true }).click();
    await screen
      .getByRole("textbox", { name: "Review evidence and reason" })
      .fill("Domain owner reviewed the source and applicability.");
    await screen
      .getByRole("checkbox", {
        name: "Share verified knowledge with authorized agents using this Semantic View",
      })
      .click();
    await screen.getByRole("button", { name: action, exact: true }).click();
    await expect.element(screen.getByText("Review saved.")).toBeVisible();
    expect(post).toHaveBeenCalledWith(
      "/agents/agent/memories/memory/review",
      expect.objectContaining({
        expected_revision: 3,
        operation: "verify",
        definition_kind: kind,
        ...(name ? { definition_name: name } : {}),
        semantic: { view_id: "view", version: 7, fingerprint: "published" },
        publish_shared: true,
      }),
    );
    if (kind === "heuristic")
      expect(post.mock.calls[0][1]).not.toHaveProperty("definition_name");
  },
);
