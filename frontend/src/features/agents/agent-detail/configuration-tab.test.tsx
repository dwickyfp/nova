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
  visibility: "private",
  created_at: "2026-09-22T00:00:00Z",
  updated_at: "2026-09-22T00:00:00Z",
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AgentConfigurationTab", () => {
  it("shows runtime mode, compiled contract, and separate skill availability modes", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
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
      .toBeVisible();
    await expect
      .element(screen.getByText("Analyze governed revenue metrics"))
      .toBeVisible();
    await expect.element(screen.getByText("semantic_query")).toBeVisible();

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
});

function response(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}
