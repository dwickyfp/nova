import { afterEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/index.css";
import { ThreadTraceView } from "./thread-trace-view";

const TRACE = {
  thread_id: "thread-1",
  title: "Revenue by category",
  user_name: "nova_admin",
  agent_id: "agent-1",
  created_at: "2026-09-21T10:00:00Z",
  updated_at: "2026-09-21T10:00:03Z",
  total_tokens: 120,
  turns: [
    {
      message_id: "user-1",
      seq: 1,
      role: "user",
      content: "Show revenue by category",
      model_name: null,
      prompt_tokens: null,
      completion_tokens: null,
      total_tokens: null,
      created_at: "2026-09-21T10:00:00Z",
      steps: [],
      instructions: null,
    },
    {
      message_id: "assistant-1",
      seq: 2,
      role: "assistant",
      content: "Electronics leads.",
      model_name: "gpt-test",
      prompt_tokens: 100,
      completion_tokens: 20,
      total_tokens: 120,
      created_at: "2026-09-21T10:00:03Z",
      instructions: "Answer from the semantic model.",
      steps: [
        {
          kind: "provider",
          step_id: "provider-1",
          purpose: "planning",
          status: "done",
          started_offset_ms: 0,
          duration_ms: 800,
        },
        {
          kind: "tool",
          step_id: "tool-1",
          tool_call_id: "call-1",
          name: "semantic_query",
          preview: "semantic_query: revenue by category",
          arguments: { question: "revenue by category" },
          status: "done",
          started_offset_ms: 810,
          duration_ms: 1200,
          trace_detail: {
            kind: "semantic_context",
            semantic_model: {
              id: "model-1",
              name: "Sales model",
              ossie_version: "0.1.1",
            },
            question: "revenue by category",
            datasets: [{ name: "orders", source: "NOVA_DEMO.orders" }],
            metrics: ["revenue"],
            generated_sql:
              "SELECT category, SUM(total) FROM NOVA_DEMO.orders GROUP BY category",
            confidence: 0.98,
            validation_warnings: [],
            generation_duration_ms: 500,
            execution_duration_ms: 700,
          },
        },
        {
          kind: "text",
          content_index: 0,
          content_id: "content-0",
          text: "Electronics leads.",
        },
        {
          kind: "table",
          content_index: 1,
          content_id: "content-1",
          tool_call_id: "call-1",
          title: "Revenue by category",
          columns: ["Category", "Revenue"],
          rows: [["Electronics", 154291000]],
        },
        { kind: "answer" },
      ],
    },
    {
      message_id: "user-2",
      seq: 3,
      role: "user",
      content: "Turn it into a chart",
      model_name: null,
      prompt_tokens: null,
      completion_tokens: null,
      total_tokens: null,
      created_at: "2026-09-21T10:00:04Z",
      steps: [],
      instructions: null,
    },
    {
      message_id: "assistant-2",
      seq: 4,
      role: "assistant",
      content: "Here is the chart.",
      model_name: "gpt-test",
      prompt_tokens: 20,
      completion_tokens: 10,
      total_tokens: 30,
      created_at: "2026-09-21T10:00:05Z",
      instructions: "Answer from the semantic model.",
      steps: [
        {
          kind: "provider",
          step_id: "provider-2",
          purpose: "response",
          status: "done",
          started_offset_ms: 0,
          duration_ms: 400,
        },
        {
          kind: "text",
          content_index: 0,
          content_id: "content-2",
          text: "Here is the chart.",
        },
        { kind: "answer" },
      ],
    },
  ],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ThreadTraceView", () => {
  it("replays structured output and links a table to its SQL trace", async () => {
    await page.viewport(1440, 520);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(TRACE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const screen = await render(
      <QueryClientProvider client={client}>
        <ThreadTraceView
          agentId="agent-1"
          threadId="thread-1"
          onClose={() => {}}
        />
      </QueryClientProvider>,
    );

    await expect
      .element(screen.getByRole("cell", { name: "Electronics" }))
      .toBeInTheDocument();
    for (const pane of ["conversation", "thread", "detail"]) {
      const shell = screen.container.querySelector<HTMLElement>(
        `[data-testid="${pane}-pane-shell"]`,
      );
      expect(shell?.classList.contains("lg:flex")).toBe(true);
    }
    const header = screen.container.querySelector<HTMLElement>("header");
    const conversationSection = screen.container.querySelector<HTMLElement>(
      '[data-testid="conversation-pane-shell"] > .nova-trace-pane-content > section',
    );
    expect(header).not.toBeNull();
    expect(conversationSection).not.toBeNull();
    expect(
      Math.abs(
        (conversationSection?.getBoundingClientRect().top ?? 0) -
          (header?.getBoundingClientRect().bottom ?? 0),
      ),
    ).toBeLessThanOrEqual(1);
    const preview = screen.container.querySelector<HTMLElement>(
      '[data-testid="conversation-preview"]',
    );
    expect(preview?.classList.contains("nova-observability-preview")).toBe(
      true,
    );
    expect(window.getComputedStyle(preview!).fontSize).toBe("12px");
    expect(window.getComputedStyle(preview!).getPropertyValue("zoom")).toBe(
      "1",
    );
    await expect
      .element(
        screen
          .getByTestId("thread-pane-shell")
          .getByText("Agent turn 1", { exact: true }),
      )
      .toBeVisible();
    await expect.element(screen.getByText("Semantic Context")).toBeVisible();

    const conversationViewport = screen.container.querySelector<HTMLElement>(
      '[data-testid="conversation-pane-shell"] [data-slot="scroll-area-viewport"]',
    );
    const secondConversationTurn = screen.container.querySelector<HTMLElement>(
      '[data-testid="conversation-turn-2"]',
    );
    expect(conversationViewport).not.toBeNull();
    expect(secondConversationTurn).not.toBeNull();
    await screen
      .getByTestId("thread-pane-shell")
      .getByText("Agent turn 2", { exact: true })
      .click();
    await vi.waitFor(() => {
      expect(conversationViewport?.scrollTop ?? 0).toBeGreaterThan(0);
    });
    await vi.waitFor(() => {
      const viewportBounds = conversationViewport!.getBoundingClientRect();
      const turnBounds = secondConversationTurn!.getBoundingClientRect();
      expect(turnBounds.top).toBeLessThan(viewportBounds.bottom);
      expect(turnBounds.bottom).toBeGreaterThan(viewportBounds.top);
    });
    await expect
      .element(
        screen
          .getByTestId("detail-pane-shell")
          .getByText("Agent turn 2", { exact: true }),
      )
      .toBeVisible();

    const tableOutput = screen.container.querySelector<HTMLElement>(
      '[data-content-id="content-1"]',
    );
    expect(tableOutput).not.toBeNull();
    await tableOutput?.click();

    const sqlRows = screen.getByText("SQL Execution", { exact: true });
    await expect.element(sqlRows.first()).toBeInTheDocument();
    await expect
      .element(screen.getByText("Generated SQL", { exact: true }))
      .toBeInTheDocument();
    await expect.element(screen.getByText("700ms").first()).toBeInTheDocument();

    await screen.getByRole("button", { name: "Collapse Conversation" }).click();
    await expect
      .element(screen.getByTestId("conversation-pane-shell"))
      .toHaveAttribute("data-collapsed", "true");
    const expandConversation = screen.getByRole("button", {
      name: "Expand Conversation",
    });
    await expect.element(expandConversation.first()).toBeInTheDocument();
    await expandConversation.first().click();
    await expect
      .element(screen.getByTestId("conversation-pane-shell"))
      .toHaveAttribute("data-collapsed", "false");
    await expect
      .element(screen.getByRole("button", { name: "Collapse Conversation" }))
      .toBeInTheDocument();

    await screen
      .getByRole("button", { name: "Collapse Thread details" })
      .click();
    await expect
      .element(screen.getByTestId("thread-pane-shell"))
      .toHaveAttribute("data-collapsed", "true");
    await screen
      .getByRole("button", { name: "Expand Thread details" })
      .first()
      .click();
    await expect
      .element(screen.getByRole("button", { name: "Collapse Thread details" }))
      .toBeInTheDocument();

    await screen
      .getByRole("button", { name: "Collapse SQL Execution" })
      .click();
    await expect
      .element(screen.getByTestId("detail-pane-shell"))
      .toHaveAttribute("data-collapsed", "true");
    await screen
      .getByRole("button", { name: "Expand Trace detail" })
      .first()
      .click();
    await expect
      .element(screen.getByRole("button", { name: "Collapse SQL Execution" }))
      .toBeInTheDocument();
  });
});
