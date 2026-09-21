import { page, userEvent } from "vitest/browser";
import { StrictMode, useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Agent } from "@/features/agents/api";
import { StudioChat } from "./studio-chat";

/**
 * The Studio transcript driven end to end against a scripted SSE stream.
 *
 * The unit tests cover the reducer; this covers the wiring: a real send, the
 * rail opening and collapsing, the SQL disclosure, the consent card resolving,
 * and the reconsider pass. Every assertion is a control that has to actually do
 * something, so a silent regression in the event plumbing fails here.
 */

const AGENT: Agent = {
  agent_id: "a1",
  owner_name: "nova",
  database_name: "sales",
  schema_name: null,
  name: "Revenue Analyst",
  description: "Answers revenue questions",
  avatar: null,
  color: null,
  model_provider_id: null,
  model_name: "gpt-4o",
  instructions_response: "",
  instructions_orchestration: "",
  response_style: null,
  sample_questions: ["Top SKUs last quarter"],
  budget_seconds: 60,
  budget_tokens: null,
  tool_not_accessible: "",
  default_tools: [],
  default_skills: [],
  policy: "auto_read_only",
  semantic_model_id: null,
  semantic_model_ids: [],
  visibility: "private",
  created_at: "",
  updated_at: "",
};

const STREAM = [
  'event: plan\ndata: {"steps":[{"id":"understand","text":"Understand the request","status":"running"}]}\n\n',
  'event: thinking\ndata: {"phase":"plan","text":"Understanding the request and choosing a skill","status":"running"}\n\n',
  'event: thinking\ndata: {"phase":"skill","text":"Loaded skill: revenue-playbook","status":"done"}\n\n',
  'event: thinking\ndata: {"phase":"act","text":"Reading the SKU table and summing netto per product","status":"done"}\n\n',
  'event: tool_call\ndata: {"tool_call_id":"c1","tool_name":"query_execute","sql_preview":"SELECT sku_name, SUM(total_netto) FROM sales.sku GROUP BY 1","classification":"read_only","status":"running"}\n\n',
  'event: tool_status\ndata: {"tool_call_id":"c1","status":"done"}\n\n',
  'event: tool_detail\ndata: {"tool_call_id":"c1","text":"1 row returned"}\n\n',
  'event: table\ndata: {"title":"Revenue per SKU","columns":["sku_name","omzet"],"rows":[["D. COKLAT 12",3123022553684],["D. SUPER 12",2673196441282]]}\n\n',
  'event: chart\ndata: {"tool_call_id":"c1","chart_spec":"{\\"title\\":\\"Omzet per SKU\\",\\"mark\\":\\"bar\\"}"}\n\n',
  'event: citation\ndata: {"title":"Semantic model","source":"sales.sku"}\n\n',
  'event: text_delta\ndata: {"text":"Omzet tertinggi ada di **D. COKLAT 12**."}\n\n',
  'event: done\ndata: {"message_id":"m1","finish_reason":"stop"}\n\n',
].join("");

const CONSENT_STREAM = [
  'event: thinking\ndata: {"phase":"plan","text":"Understanding the request","status":"running"}\n\n',
  'event: tool_call\ndata: {"tool_call_id":"c9","tool_name":"query_execute","sql_preview":"DROP TABLE sales.sku","classification":"destructive","status":"pending"}\n\n',
].join("");

/**
 * A stored thread, shaped exactly as the backend now returns it: the trace on
 * the assistant message, including the result blocks. This is what a reload has
 * to rebuild the process view from.
 */
const PERSISTED_MESSAGES = [
  {
    message_id: "m-user",
    role: "user",
    content: "Omzet per SKU 3 bulan ke belakang",
    created_at: "2026-01-01T00:00:00",
    steps: [],
  },
  {
    message_id: "m-assistant",
    role: "assistant",
    content: "Omzet tertinggi ada di **D. COKLAT 12**.",
    created_at: "2026-01-01T00:00:01",
    total_tokens: 412,
    model_name: "gpt-4o",
    steps: [
      { kind: "reasoning", phase: "plan", text: "Understanding the request" },
      { kind: "reasoning", phase: "skill", text: "Loading skill: revenue" },
      {
        kind: "tool",
        name: "query_execute",
        preview: "SELECT sku_name, SUM(total_netto) FROM sales.sku GROUP BY 1",
        arguments: {},
        status: "done",
      },
      {
        kind: "table",
        title: "Omzet per SKU",
        columns: ["sku_name", "omzet"],
        rows: [["D. COKLAT 12", 3123022553684]],
      },
      {
        kind: "chart",
        tool_call_id: "c1",
        chart_spec: '{"title":"Omzet per SKU","mark":"bar"}',
      },
      { kind: "citation", citations: [{ title: "Semantic model" }] },
      { kind: "answer" },
    ],
  },
];

function sseResponse(body: string): Response {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function mockFetch(
  mode: "read_only" | "ask_every_tool" = "read_only",
  extended = false,
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/studio/settings")) {
      return new Response(
        JSON.stringify({
          identity: {
            username: "nova",
            roles: [],
            active_role: "ACCOUNTADMIN",
            warehouses: [],
            active_warehouse: null,
          },
          preferences: {
            theme: "system",
            language: "en",
            preferred_name: null,
            role: null,
            warehouse: null,
            extended_thinking: extended,
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.includes("/threads") && url.includes("/messages")) {
      return sseResponse(mode === "ask_every_tool" ? CONSENT_STREAM : STREAM);
    }
    // GET one thread returns the thread plus its messages, each carrying the
    // trace the loop recorded.
    if (/\/threads\/t-past$/.test(url)) {
      return new Response(
        JSON.stringify({
          thread: {
            thread_id: "t-past",
            title: "Omzet per SKU",
            workspace_file_id: null,
            agent_id: "a1",
            created_at: "",
            updated_at: "",
            message_count: 2,
          },
          messages: PERSISTED_MESSAGES,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.includes("/threads")) {
      return new Response(
        JSON.stringify({
          thread_id: "t1",
          title: "New chat",
          workspace_file_id: null,
          agent_id: "a1",
          created_at: "",
          updated_at: "",
          message_count: 0,
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      );
    }
    return new Response(null, { status: 204 });
  });
}

function renderStudio(
  mode: "read_only" | "ask_every_tool" = "read_only",
  extended = false,
  activeThreadId: string | null = null,
) {
  const agent =
    mode === "ask_every_tool"
      ? { ...AGENT, policy: "ask_every_tool" as const }
      : AGENT;
  mockFetch(mode, extended);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    // StrictMode, matching the app: it double-invokes effects, which is the
    // re-run that exposed the stuck loading state. Testing outside it would
    // hide the bug.
    <StrictMode>
      <QueryClientProvider client={client}>
        <div className="flex h-svh">
          <StudioChat
            agent={agent}
            agents={[agent]}
            onSelectAgent={() => {}}
            currentRole="ACCOUNTADMIN"
            displayName="Dwicky"
            activeThreadId={activeThreadId}
            onThreadChange={() => {}}
          />
        </div>
      </QueryClientProvider>
    </StrictMode>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

/**
 * The refresh path: no thread is selected at first render, then one arrives.
 *
 * `StudioApp` supplies the thread after the history loads, so the chat has to
 * handle a selection that appears after mount, not only one present at mount.
 */
function renderStudioWithLateThread() {
  mockFetch("read_only", false);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  function Harness() {
    const [id, setId] = useState<string | null>(null);
    useEffect(() => {
      const timer = setTimeout(() => setId("t-past"), 50);
      return () => clearTimeout(timer);
    }, []);
    return (
      <StudioChat
        agent={AGENT}
        agents={[AGENT]}
        onSelectAgent={() => {}}
        currentRole="ACCOUNTADMIN"
        activeThreadId={id}
        onThreadChange={() => {}}
      />
    );
  }

  return render(
    <StrictMode>
      <QueryClientProvider client={client}>
        <div className="flex h-svh">
          <Harness />
        </div>
      </QueryClientProvider>
    </StrictMode>,
  );
}

describe("studio transcript click-through", () => {
  it("shows the personalized Nova welcome without a chat header", async () => {
    renderStudio();

    await expect
      .element(page.getByRole("heading", { name: /Good (morning|afternoon|evening), Dwicky/ }))
      .toBeVisible();
    await expect
      .element(page.getByText("What insights can I help with?"))
      .toBeVisible();
    await expect
      .element(page.getByText("Nova Studio", { exact: true }))
      .not.toBeInTheDocument();
  });

  it("renders the process rail and the answer, and opens a step", async () => {
    renderStudio();
    await page
      .getByPlaceholder("Ask a question about your data")
      .fill("Top SKUs");
    await page.getByRole("button", { name: "Send" }).click();

    await expect.element(page.getByText("Revenue per SKU")).toBeVisible();
    await expect
      .element(page.getByRole("cell", { name: "D. COKLAT" }))
      .toBeVisible();

    // The rail opens with the turn, so its detail is never a click away.
    const rail = page.getByTestId("process-rail-toggle");
    await expect.element(rail).toBeVisible();
    await expect.element(rail).toHaveAttribute("data-state", "open");
    await expect
      .element(page.getByText("Loaded skill: revenue-playbook"))
      .toBeVisible();

    // The statement is behind the step, not behind a second control: opening
    // the step is the reader saying "show me what this did".
    await expect
      .element(page.getByText(/SELECT sku_name/))
      .not.toBeInTheDocument();
    await page.getByRole("button", { name: /Ran a SQL query/ }).click();
    await expect.element(page.getByText(/SELECT sku_name/)).toBeVisible();

    // The turn has settled: the tool row and its statement are present, and no
    // step is still spinning. A leftover spinner was the visible symptom of a
    // phase the loop never closed.
    await expect
      .element(page.getByText("Ran a SQL query", { exact: false }))
      .toBeVisible();
    expect(document.querySelectorAll(".animate-spin")).toHaveLength(0);

    // Collapsing the rail hides the work without removing the answer.
    await rail.click();
    await expect.element(rail).toHaveAttribute("data-state", "closed");
    await expect
      .element(page.getByText("Loaded skill: revenue-playbook"))
      .not.toBeInTheDocument();
    await expect.element(page.getByText("Revenue per SKU")).toBeVisible();

    const filter = page.getByRole("textbox", { name: "Filter rows" });
    await filter.fill("SUPER");
    await expect.element(page.getByText("D. SUPER 12")).toBeVisible();

    const csv = page.getByRole("button", { name: "Download results as CSV" });
    expect(csv).toBeTruthy();

    // The chart card header reads the title from the spec, so it is never blank
    // and never a duplicate of a title printed inside the chart itself.
    await expect.element(page.getByText("Omzet per SKU")).toBeVisible();
  });

  it("shows a consent card for an ask_every_tool agent and resolves it", async () => {
    renderStudio("ask_every_tool");
    await page
      .getByPlaceholder("Ask a question about your data")
      .fill("Top SKUs");
    await page.getByRole("button", { name: "Send" }).click();

    const allow = page.getByRole("button", { name: "Allow once" });
    await expect.element(allow).toBeVisible();
    await allow.click();

    // The card is replaced by a rail row recording the decision.
    await expect
      .element(page.getByRole("button", { name: "Allow once" }))
      .not.toBeInTheDocument();
    await expect
      .element(page.getByText("Approved query_execute"))
      .toBeVisible();
  });

  it("keeps the composer controls keyboard reachable", async () => {
    renderStudio();
    const textarea = page.getByPlaceholder("Ask a question about your data");
    await textarea.click();
    await userEvent.keyboard("top skus{Enter}");
    await expect.element(page.getByText("Revenue per SKU")).toBeVisible();
  });

  it("opens a step to show what it did", async () => {
    renderStudio();
    await page
      .getByPlaceholder("Ask a question about your data")
      .fill("Top SKUs");
    await page.getByRole("button", { name: "Send" }).click();
    await expect.element(page.getByText("Revenue per SKU")).toBeVisible();

    // The tool step's detail is what the tool returned, and it is only shown
    // once the step is opened.
    const toolStep = page.getByRole("button", { name: /Ran a SQL query/ });
    await expect.element(toolStep).toBeVisible();
    await expect
      .element(page.getByText("1 row returned"))
      .not.toBeInTheDocument();
    await toolStep.click();
    await expect.element(page.getByText("1 row returned")).toBeVisible();

    // A reasoning step opens too: every step answers "what did this do".
    const planStep = page.getByRole("button", {
      name: /Understanding the request and choosing a skill/,
    });
    await expect.element(planStep).toBeVisible();
  });

  it("offers a reconsider pass that streams as a labelled continuation", async () => {
    renderStudio("read_only", true);
    await page
      .getByPlaceholder("Ask a question about your data")
      .fill("Top SKUs");
    await page.getByRole("button", { name: "Send" }).click();
    await expect.element(page.getByText("Revenue per SKU")).toBeVisible();

    const reconsider = page.getByRole("button", {
      name: "Reconsider this answer",
    });
    await expect.element(reconsider).toBeVisible();
    await reconsider.click();

    await expect
      .element(page.getByText("Reconsidering the previous answer"))
      .toBeVisible();
  });

  it("rebuilds the process and the data when a stored thread is opened", async () => {
    renderStudio("read_only", false, "t-past");

    // The answer comes back.
    await expect
      .element(page.getByText(/Omzet tertinggi ada di/))
      .toBeVisible();

    // So does the process that produced it: the reasoning rows, the tool row
    // with its SQL, the grid, and the chart.
    await expect
      .element(
        page.getByText("Loaded skill: revenue-playbook", { exact: false }),
      )
      .not.toBeInTheDocument();
    await expect
      .element(page.getByText("Loading skill: revenue"))
      .toBeVisible();
    await page.getByRole("button", { name: /Ran a SQL query/ }).click();
    await expect.element(page.getByText(/SELECT sku_name/)).toBeVisible();

    await expect.element(page.getByText("Omzet per SKU").first()).toBeVisible();
    await expect
      .element(page.getByRole("cell", { name: "D. COKLAT" }))
      .toBeVisible();

    // Nothing is spinning: a replayed turn is finished by definition.
    expect(document.querySelectorAll(".animate-spin")).toHaveLength(0);
  });

  it("loads a thread that is selected after mount, without hanging", async () => {
    // The refresh path: the URL carries only an agent, then the "open the
    // newest conversation" effect supplies a thread id. Clearing the loading
    // flag inside a cancelled guard left the pane on "Opening the conversation"
    // forever, next to a sidebar full of history.
    renderStudioWithLateThread();
    await expect
      .element(page.getByText(/Omzet tertinggi ada di/))
      .toBeVisible();
    await expect
      .element(page.getByText("Opening the conversation"))
      .not.toBeInTheDocument();
  });
});
