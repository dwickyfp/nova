import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/index.css";
import type { Agent } from "@/features/agents/api";
import { AgentConfigurationTab } from "./configuration-tab";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

const AGENT: Agent = {
  agent_id: "agent-1",
  owner_name: "nova_admin",
  database_name: null,
  schema_name: null,
  name: "Revenue analyst",
  description: "Analyze governed revenue metrics",
  avatar: null,
  color: null,
  model_provider_id: null,
  model_name: null,
  instructions_response: "Keep answers concise.",
  instructions_orchestration: "Always use semantic metrics.",
  response_style: null,
  sample_questions: [],
  budget_seconds: null,
  budget_tokens: null,
  tool_not_accessible: "accept",
  default_tools: ["semantic_query"],
  default_skills: [],
  discoverable_skills: [],
  compiled_instructions: {
    version: 1,
    mission: "Analyze governed revenue metrics",
    must_do: ["Always use semantic metrics"],
    must_not: [],
    preferred_capabilities: ["semantic_query"],
    rejected_rules: [],
  },
  harness_mode: "guided",
  policy: "auto_read_only",
  semantic_model_id: null,
  semantic_model_ids: [],
  semantic_view_ids: [],
  visibility: "private",
  created_at: "2026-09-22T00:00:00Z",
  updated_at: "2026-09-22T00:00:00Z",
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AgentConfigurationTab", () => {
  it("keeps business tools and removes legacy free-form SQL on save", async () => {
    const updates: Record<string, unknown>[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      if (init?.method === "PUT") {
        updates.push(JSON.parse(init.body as string));
        return response(AGENT);
      }
      const url = String(input);
      if (url.endsWith("/tools")) return response({ tools: [
        { name: "query_execute", source: "builtin", description: "Free-form SQL" },
        { name: "semantic_query", source: "builtin", description: "Business metrics" },
      ] });
      if (url.endsWith("/custom-tools")) return response({ tools: [] });
      if (url.endsWith("/semantic-views")) return response([]);
      return response({});
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const screen = await render(
      <QueryClientProvider client={client}>
        <AgentConfigurationTab
          agent={{ ...AGENT, default_tools: ["query_execute", "semantic_query"] }}
          onSaved={() => {}}
        />
      </QueryClientProvider>,
    );
    await screen.getByRole("tab", { name: "Tools" }).click();
    await expect.element(screen.getByText("semantic_query", { exact: true })).toBeVisible();
    await expect.element(screen.getByText("query_execute", { exact: true })).not.toBeInTheDocument();
    await expect.element(screen.getByRole("checkbox", { name: /semantic_query/ })).toBeChecked();
    await screen.getByRole("button", { name: "Save changes" }).click();
    await vi.waitFor(() => expect(updates[0]?.default_tools).toEqual(["semantic_query"]));
  });

  it("resets an unsaved custom tool edit when the dialog is reopened", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/custom-tools")) {
        return response({
          tools: [
            {
              tool_id: "tool-1",
              owner_name: "nova_admin",
              name: "LOOKUP",
              description: "Look up one row",
              kind: "procedure",
              database_name: null,
              function_name: null,
              definition: {
                parameters: [],
                statements: ["SELECT 1"],
                output_mode: "result",
              },
              created_at: "2026-09-22T00:00:00Z",
              updated_at: "2026-09-22T00:00:00Z",
            },
          ],
          count: 1,
        });
      }
      if (url.endsWith("/tools")) return response({ tools: [] });
      if (url.endsWith("/semantic-views")) return response([]);
      if (url.endsWith("/semantic-views")) return response([]);
      return response(AGENT);
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const screen = await render(
      <QueryClientProvider client={client}>
        <AgentConfigurationTab agent={AGENT} onSaved={() => {}} />
      </QueryClientProvider>,
    );

    await screen.getByRole("tab", { name: "Tools" }).click();
    await screen.getByRole("button", { name: "Edit LOOKUP" }).click();
    const name = screen.getByRole("textbox", { name: "Name" });
    await name.fill("CHANGED");
    await screen.getByRole("button", { name: "Cancel" }).click();
    await screen.getByRole("button", { name: "Edit LOOKUP" }).click();
    await expect
      .element(screen.getByRole("textbox", { name: "Name" }))
      .toHaveValue("LOOKUP");
  });

  it("saves the selected provider and model and restores the default", async () => {
    const updates: Record<string, unknown>[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      if (init?.method === "PUT") {
        updates.push(JSON.parse(init.body as string));
        return response(AGENT);
      }
      const url = String(input);
      if (url.endsWith("/semantic-views")) return response([]);
      if (url.endsWith("/ai/providers")) {
        return response({
          providers: [
            { id: "a", name: "Provider A", is_active: true, has_api_key: true },
            { id: "b", name: "Provider B", is_active: true, has_api_key: true },
          ],
        });
      }
      const provider = url.includes("/a/models") ? "a" : "b";
      return response({
        models: [
          {
            id: `model-${provider}`,
            name: "shared-model",
            display_name: "Chat model",
            type: "llm",
            is_active: true,
          },
          {
            id: `embedding-${provider}`,
            name: "Embedding",
            type: "embedding",
            is_active: true,
          },
          {
            id: `inactive-${provider}`,
            name: "Inactive",
            type: "llm",
            is_active: false,
          },
        ],
      });
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const screen = await render(
      <QueryClientProvider client={client}>
        <AgentConfigurationTab
          agent={{
            ...AGENT,
            model_provider_id: "a",
            model_name: "shared-model",
          }}
          onSaved={() => {}}
        />
      </QueryClientProvider>,
    );
    await screen.getByRole("tab", { name: "Instructions" }).click();
    const select = screen.getByRole("combobox", { name: "Model", exact: true });
    await expect.element(select).toBeEnabled();
    await expect.element(select).toHaveTextContent("Chat model · Provider A");
    await select.click();
    await expect
      .element(screen.getByRole("option", { name: "Embedding" }))
      .not.toBeInTheDocument();
    await expect
      .element(screen.getByRole("option", { name: "Inactive" }))
      .not.toBeInTheDocument();
    await screen
      .getByRole("option", { name: "Chat model · Provider B" })
      .click();
    await screen.getByRole("button", { name: "Save changes" }).click();
    await vi.waitFor(() =>
      expect(updates[0]).toMatchObject({
        model_provider_id: "b",
        model_name: "shared-model",
      }),
    );
    await select.click();
    await screen
      .getByRole("option", { name: "Provider default", exact: true })
      .click();
    await screen.getByRole("button", { name: "Save changes" }).click();
    await vi.waitFor(() =>
      expect(updates[1]).toMatchObject({
        model_provider_id: null,
        model_name: null,
      }),
    );
  });

  it("shows runtime mode, compiled contract, and separate skill availability modes", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/semantic-views")) return response([]);
        if (url.endsWith("/ai/providers")) return response({ providers: [] });
        if (url.endsWith("/skills")) {
          return response({
            skills: [
              {
                skill_id: "skill-1",
                owner_name: "nova_admin",
                name: "revenue-analysis",
                description: "Revenue analysis procedure",
                body: "",
                scope: "user",
                source: "user",
                read_only: false,
                created_at: "2026-09-22T00:00:00Z",
                updated_at: "2026-09-22T00:00:00Z",
              },
            ],
            count: 1,
          });
        }
        return response(AGENT);
      });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const screen = await render(
      <QueryClientProvider client={client}>
        <AgentConfigurationTab agent={AGENT} onSaved={() => {}} />
      </QueryClientProvider>,
    );

    await expect
      .element(screen.getByText("Automatic runtime orchestration"))
      .toBeVisible();
    await expect
      .element(screen.getByText(/No manual mode selection is required/))
      .toBeVisible();

    await screen.getByRole("tab", { name: "Instructions" }).click();
    await expect
      .element(screen.getByText("Compiled agent contract"))
      .not.toBeInTheDocument();
    await expect
      .element(
        screen.getByRole("textbox", { name: "Orchestration instructions" }),
      )
      .toBeVisible();

    await screen.getByRole("tab", { name: "Skills" }).click();
    const mode = screen.getByRole("combobox", {
      name: "revenue-analysis mode",
    });
    await expect.element(mode).toBeVisible();
    await mode.click();
    await screen.getByRole("option", { name: "Discoverable" }).click();
    await screen.getByRole("button", { name: "Save changes" }).click();

    await vi.waitFor(() => {
      const update = fetchMock.mock.calls.find(
        ([, init]) => init?.method === "PUT",
      );
      expect(update).toBeDefined();
      const body = JSON.parse(update?.[1]?.body as string);
      expect(body.discoverable_skills).toEqual(["revenue-analysis"]);
      expect(body.default_skills).toEqual([]);
      expect(body.harness_mode).toBe("auto");
      expect(body.compiled_instructions).toBeUndefined();
    });
  });

  it("lets the owner explicitly enable an external MCP tool", async () => {
    const updates: Record<string, unknown>[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      if (init?.method === "PUT") {
        updates.push(JSON.parse(init.body as string));
        return response(AGENT);
      }
      const url = String(input);
      if (url.endsWith("/semantic-views")) return response([]);
      if (url.endsWith("/agents/tools"))
        return response({
          tools: [
            {
              tool_id: "tool-1",
              owner_name: "__nova__",
              name: "lookup_customer",
              description: "Look up a customer in CRM",
              source: "mcp:server-1",
              input_schema: {},
              is_enabled: true,
            },
          ],
          count: 1,
        });
      if (url.endsWith("/ai/providers")) return response({ providers: [] });
      return response({ tools: [], skills: [], servers: [], count: 0 });
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const screen = await render(
      <QueryClientProvider client={client}>
        <AgentConfigurationTab agent={AGENT} onSaved={() => {}} />
      </QueryClientProvider>,
    );
    await screen.getByRole("tab", { name: "Tools" }).click();
    await expect
      .element(screen.getByText("External calls always require approval."))
      .toBeVisible();
    await screen.getByRole("checkbox", { name: /lookup_customer/ }).click();
    await screen.getByRole("button", { name: "Save changes" }).click();
    await vi.waitFor(() =>
      expect(updates[0]?.default_tools).toContain("mcp:tool-1"),
    );
  });

  it("binds only published Semantic Views using semantic_view_ids", async () => {
    const updates: Record<string, unknown>[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      if (init?.method === "PUT") {
        updates.push(JSON.parse(init.body as string));
        return response(AGENT);
      }
      const url = String(input);
      if (url.endsWith("/semantic-views")) return response([
        { id: "view-active", name: "Sales", database_name: "SALES",
          status: "ACTIVE", active_version: 2 },
        { id: "view-draft", name: "Forecast", database_name: "SALES",
          status: "DRAFT", active_version: null },
      ]);
      if (url.endsWith("/agents/tools")) return response({ tools: [] });
      return response({ tools: [], servers: [], count: 0 });
    });
    const screen = await render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AgentConfigurationTab agent={AGENT} onSaved={() => {}} />
      </QueryClientProvider>,
    );
    await screen.getByRole("tab", { name: "Tools" }).click();
    await screen.getByRole("combobox", { name: "Select a semantic view" }).click();
    await expect.element(screen.getByRole("option", { name: /Sales/ })).toBeVisible();
    await expect.element(screen.getByRole("option", { name: /Forecast/ })).not.toBeInTheDocument();
    await screen.getByRole("option", { name: /Sales/ }).click();
    await screen.getByRole("button", { name: "Add Semantic View" }).click();
    await screen.getByRole("button", { name: "Save changes" }).click();
    await vi.waitFor(() => expect(updates[0]?.semantic_view_ids).toEqual(["view-active"]));
    expect(updates[0]?.semantic_model_ids).toBeUndefined();
  });
});

function response(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}
