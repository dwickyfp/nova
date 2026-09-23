import { page, userEvent } from "vitest/browser";
import { StrictMode, useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Agent } from "@/features/agents/api";
import { StudioChat } from "./studio-chat";
import "@/styles/index.css";

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
    attachments: [
      { name: "report.pdf", media_type: "application/pdf", size_bytes: 640 },
      { name: "chart.png", media_type: "image/png", size_bytes: 128 },
    ],
    steps: [],
  },
  {
    message_id: "m-assistant",
    role: "assistant",
    content: "Omzet tertinggi ada di **D. COKLAT 12**.",
    created_at: "2026-01-01T00:00:01",
    total_tokens: 412,
    feedback: "like",
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
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
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
    if (url.endsWith("/feedback")) {
      return new Response(String(init?.body), { status: 200, headers: { "Content-Type": "application/json" } });
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
  sampleQuestions = AGENT.sample_questions,
) {
  const baseAgent =
    mode === "ask_every_tool"
      ? { ...AGENT, policy: "ask_every_tool" as const }
      : AGENT;
  const agent = { ...baseAgent, sample_questions: sampleQuestions };
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

/**
 * Reproduces the router race behind New chat. The chat reset is synchronous,
 * while the search param can still expose the previous thread for one or more
 * renders. That stale prop must not reopen the conversation we just left.
 */
function renderStudioWithStaleThreadAfterNewChat() {
  mockFetch("read_only", false);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  function Harness() {
    const [threadId, setThreadId] = useState<string | null>("t-past");
    const [newChatNonce, setNewChatNonce] = useState(0);
    return (
      <>
        <button
          type="button"
          onClick={() => setNewChatNonce((value) => value + 1)}
        >
          Start fresh
        </button>
        <StudioChat
          agent={AGENT}
          agents={[AGENT]}
          onSelectAgent={() => {}}
          currentRole="ACCOUNTADMIN"
          activeThreadId={threadId}
          onThreadChange={setThreadId}
          newChatNonce={newChatNonce}
        />
      </>
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
  it("attaches a text file, removes it, then sends the selected file to the agent", async () => {
    renderStudio();
    const chooser = page.getByLabelText("Choose files");
    const first = new File(["discard me"], "draft.txt", { type: "text/plain" });
    await userEvent.upload(chooser, first);
    await expect.element(page.getByRole("button", { name: "Remove draft.txt" })).toBeVisible();
    await userEvent.click(page.getByRole("button", { name: "Remove draft.txt" }));
    await expect.element(page.getByText("draft.txt")).not.toBeInTheDocument();

    const selected = new File(["The answer is 42."], "facts.txt", { type: "text/plain" });
    await userEvent.upload(chooser, selected);
    await userEvent.fill(page.getByRole("textbox", { name: "Message Nova" }), "Read the attachment");
    await userEvent.click(page.getByRole("button", { name: "Send", exact: true }));
    await expect.element(page.getByText("facts.txt")).toBeVisible();
    await expect.element(page.getByText(/Omzet tertinggi/)).toBeVisible();
    const messageCall = vi.mocked(globalThis.fetch).mock.calls.find(([url]) =>
      String(url).includes("/threads/") && String(url).endsWith("/messages"),
    );
    expect(JSON.parse(String(messageCall?.[1]?.body))).toMatchObject({
      content: "Read the attachment",
      attachments: [{ name: "facts.txt", content: "The answer is 42." }],
    });
    expect(String(messageCall?.[1]?.body)).not.toContain("discard me");
  });

  it("allows a file to be sent without typed text", async () => {
    renderStudio();
    await userEvent.upload(
      page.getByLabelText("Choose files"),
      new File(["The answer is 42."], "facts.txt", { type: "text/plain" }),
    );
    await expect.element(page.getByRole("button", { name: "Send", exact: true })).toBeEnabled();
    await userEvent.click(page.getByRole("button", { name: "Send", exact: true }));
    const messageCall = vi.mocked(globalThis.fetch).mock.calls.find(([url]) =>
      String(url).includes("/threads/") && String(url).endsWith("/messages"),
    );
    expect(JSON.parse(String(messageCall?.[1]?.body))).toMatchObject({
      content: "",
      attachments: [{ name: "facts.txt", content: "The answer is 42." }],
    });
  });

  it("attaches PDF and image files and sends their media types to the agent", async () => {
    renderStudio();
    const pdf = new File(["%PDF-1.4\n"], "facts.pdf", { type: "application/pdf" });
    const pngBase64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC";
    const pngBytes = Uint8Array.from(atob(pngBase64), (character) => character.charCodeAt(0));
    const image = new File([pngBytes], "photo.png", { type: "image/png" });
    await userEvent.upload(page.getByLabelText("Choose files"), [pdf, image]);
    await expect.element(page.getByRole("button", { name: "Remove facts.pdf" })).toBeVisible();
    await expect.element(page.getByRole("button", { name: "Remove photo.png" })).toBeVisible();
    await userEvent.fill(page.getByRole("textbox", { name: "Message Nova" }), "Read both files");
    await userEvent.click(page.getByRole("button", { name: "Send", exact: true }));
    const messageCall = vi.mocked(globalThis.fetch).mock.calls.find(([url]) =>
      String(url).includes("/threads/") && String(url).endsWith("/messages"),
    );
    expect(JSON.parse(String(messageCall?.[1]?.body))).toMatchObject({
      content: "Read both files",
      attachments: [
        { name: "facts.pdf", media_type: "application/pdf", content: btoa("%PDF-1.4\n") },
        { name: "photo.png", media_type: "image/png", content: pngBase64 },
      ],
    });
    await expect.element(page.getByText("facts.pdf")).toBeVisible();
    await expect.element(page.getByText("photo.png")).toBeVisible();
  });

  it("authors a skill from the slash command and saves only after review", async () => {
    const draft =
      "---\nname: weekly-review\ndescription: Review weekly reports\n---\nAsk for the report. Summarize changes.";
    const fetchMock = mockFetch();
    const fallback = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/messages"))
        return sseResponse(
          `event: text_delta\ndata: ${JSON.stringify({ text: "```skill\n" + draft + "\n```" })}\n\n` +
            'event: done\ndata: {"message_id":"draft1","finish_reason":"stop"}\n\n',
        );
      if (url.endsWith("/studio/skills"))
        return new Response(
          JSON.stringify({ skill_id: "s1", name: "weekly-review" }),
          { headers: { "Content-Type": "application/json" } },
        );
      return fallback(input, init);
    });
    await render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <StudioChat
          agent={AGENT}
          agents={[AGENT]}
          onSelectAgent={() => {}}
          currentRole={null}
          activeThreadId={null}
          onThreadChange={() => {}}
        />
      </QueryClientProvider>,
    );
    await userEvent.fill(
      page.getByRole("textbox", { name: "Message Nova" }),
      "/",
    );
    await userEvent.click(
      page.getByRole("button", {
        name: "/create-skill-with-chat",
        exact: true,
      }),
    );
    await expect
      .element(page.getByRole("textbox", { name: "Message Nova" }))
      .toHaveValue("/create-skill-with-chat ");
    await userEvent.fill(
      page.getByRole("textbox", { name: "Message Nova" }),
      "/create-skill-with-chat Summarize weekly reports",
    );
    await userEvent.click(
      page.getByRole("button", { name: "Send", exact: true }),
    );
    await expect
      .element(page.getByRole("button", { name: "Review and save skill" }))
      .toBeVisible();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/studio/skills"),
      ),
    ).toBe(false);
    await userEvent.click(
      page.getByRole("button", { name: "Review and save skill" }),
    );
    await expect
      .element(page.getByRole("textbox", { name: "SKILL.md" }))
      .toHaveValue(draft);
    await userEvent.click(
      page.getByRole("button", { name: "Save skill", exact: true }),
    );
    await expect
      .poll(() =>
        fetchMock.mock.calls.some(([input]) =>
          String(input).endsWith("/studio/skills"),
        ),
      )
      .toBe(true);
    const saveCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/studio/skills"),
    );
    expect(JSON.parse(String(saveCall?.[1]?.body))).toEqual({
      document: draft,
    });
  });

  it("preserves the create-with-chat prefill through initial effects", async () => {
    mockFetch();
    await render(
      <QueryClientProvider client={new QueryClient()}>
        <StudioChat
          agent={AGENT}
          agents={[AGENT]}
          onSelectAgent={() => {}}
          currentRole={null}
          activeThreadId={null}
          onThreadChange={() => {}}
          initialPrompt="/create-skill-with-chat "
        />
      </QueryClientProvider>,
    );
    await expect
      .element(page.getByRole("textbox", { name: "Message Nova" }))
      .toHaveValue("/create-skill-with-chat ");
    await expect
      .element(page.getByRole("heading", { name: "Create a skill with Nova" }))
      .toBeVisible();
  });

  it("shows the personalized Nova welcome without a chat header", async () => {
    renderStudio();

    await expect
      .element(
        page.getByRole("heading", {
          name: /Good (morning|afternoon|evening), Dwicky/,
        }),
      )
      .toBeVisible();
    await expect
      .element(page.getByText("What insights can I help with?"))
      .toBeVisible();
    await expect
      .element(page.getByText("Nova Studio", { exact: true }))
      .not.toBeInTheDocument();
  });

  it("centers the welcome composer above suggestions and moves it down on send", async () => {
    renderStudio();
    const greetingLocator = page.getByRole("heading", {
      name: /Good (morning|afternoon|evening), Dwicky/,
    });
    await expect.element(greetingLocator).toBeVisible();
    const greeting = greetingLocator.element();
    const composer = page.getByTestId("studio-composer").element();
    const textarea = page.getByRole("textbox", { name: "Message Nova" }).element();
    const suggestion = page.getByRole("button", { name: "Top SKUs last quarter" }).element();

    expect(greeting.getBoundingClientRect().bottom).toBeLessThan(
      composer.getBoundingClientRect().top,
    );
    expect(composer.getBoundingClientRect().bottom).toBeLessThanOrEqual(
      suggestion.getBoundingClientRect().top,
    );
    const welcomeTop = composer.getBoundingClientRect().top;
    const animate = vi.spyOn(Element.prototype, "animate");

    await userEvent.fill(
      page.getByRole("textbox", { name: "Message Nova" }),
      "Top SKUs",
    );
    await page.getByRole("button", { name: "Send" }).click();
    await expect.element(page.getByText("Revenue per SKU")).toBeVisible();
    await Promise.all(composer.getAnimations().map((animation) => animation.finished));

    expect(page.getByRole("textbox", { name: "Message Nova" }).element()).toBe(
      textarea,
    );
    expect(composer.getBoundingClientRect().top).toBeGreaterThan(welcomeTop);
    await expect
      .element(page.getByRole("button", { name: "Top SKUs last quarter" }))
      .not.toBeInTheDocument();
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      expect(animate).toHaveBeenCalled();
    }
  });

  it("clears the composer and shows progress while a new thread is still being created", async () => {
    renderStudio();
    const fetchMock = vi.mocked(globalThis.fetch);
    const respond = fetchMock.getMockImplementation();
    if (!respond) throw new Error("Fetch mock is unavailable");
    let releaseCreate: () => void = () => {};
    const createGate = new Promise<void>((resolve) => { releaseCreate = resolve; });
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).endsWith("/threads") && init?.method === "POST") await createGate;
      return respond(input, init);
    });

    const textbox = page.getByRole("textbox", { name: "Message Nova" });
    await expect.element(textbox).toBeVisible();
    await userEvent.fill(textbox, "Top SKUs");
    await page.getByRole("button", { name: "Send" }).click();

    await expect.element(textbox).toHaveValue("");
    await expect.element(page.getByTestId("process-rail-toggle")).toBeVisible();
    expect(page.getByTestId("process-rail-toggle").element().textContent).toContain("Starting analysis…");
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/messages"))).toBe(false);

    releaseCreate();
    await expect.element(page.getByText("Revenue per SKU")).toBeVisible();
  });

  it("restores the draft if a new conversation cannot be created", async () => {
    renderStudio();
    const fetchMock = vi.mocked(globalThis.fetch);
    const respond = fetchMock.getMockImplementation();
    if (!respond) throw new Error("Fetch mock is unavailable");
    let rejectCreate: (reason: Error) => void = () => {};
    const createGate = new Promise<void>((_, reject) => { rejectCreate = reject; });
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).endsWith("/threads") && init?.method === "POST") await createGate;
      return respond(input, init);
    });

    const textbox = page.getByRole("textbox", { name: "Message Nova" });
    await expect.element(textbox).toBeVisible();
    await userEvent.fill(textbox, "Top SKUs");
    await page.getByRole("button", { name: "Send" }).click();
    await expect.element(textbox).toHaveValue("");

    rejectCreate(new Error("Conversation unavailable"));
    await expect.element(textbox).toHaveValue("Top SKUs");
    await expect.element(page.getByRole("alert")).toHaveTextContent("Conversation unavailable");
  });

  it("keeps a long question list compact and makes every question available", async () => {
    const questions = Array.from({ length: 8 }, (_, index) => `Question ${index + 1} about sales performance`);
    renderStudio("read_only", false, null, questions);

    await expect.element(page.getByRole("textbox", { name: "Message Nova" })).toBeVisible();
    const composer = page.getByTestId("studio-composer").element();
    const composerRect = composer.getBoundingClientRect();
    const composerCenter = composerRect.top + composerRect.height / 2;
    expect(Math.abs(composerCenter - window.innerHeight / 2)).toBeLessThan(window.innerHeight / 6);
    await expect.element(page.getByRole("button", { name: questions[4] })).not.toBeInTheDocument();

    await page.getByRole("button", { name: "View all (8)" }).click();
    await expect.element(page.getByRole("button", { name: questions[7] })).toBeVisible();
    await page.getByRole("button", { name: questions[7] }).click();
    await expect.element(page.getByText(questions[7])).toBeVisible();
  });

  it("uses a white composer and a bordered text-only agent badge", async () => {
    renderStudio();
    await expect
      .element(page.getByPlaceholder("Ask a question about your data"))
      .toBeVisible();

    const textarea = document.querySelector<HTMLTextAreaElement>(
      'textarea[placeholder="Ask a question about your data"]',
    );
    expect(textarea?.parentElement?.classList.contains("bg-card")).toBe(true);

    const agentButton = [...document.querySelectorAll("button")].find(
      (button) => button.textContent?.includes("Revenue Analyst"),
    );
    expect(agentButton?.classList.contains("border")).toBe(true);
    expect(agentButton?.querySelector(".lucide-workflow")).toBeNull();
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

    // A short factual step has no duplicate disclosure. Only tool evidence or
    // genuinely additional narration is expandable.
    const planStep = page.getByRole("button", {
      name: /Understanding the request and choosing a skill/,
    });
    await expect.element(planStep).toBeVisible();
    await expect.element(planStep).toBeDisabled();
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

    await expect.element(page.getByText("report.pdf")).toBeVisible();
    await expect.element(page.getByText("chart.png")).toBeVisible();

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

  it("restores saved feedback and sends changes for the persisted assistant message", async () => {
    renderStudio("read_only", false, "t-past");
    const like = page.getByRole("button", { name: "Like answer", exact: true });
    await expect.element(like).toHaveAttribute("aria-pressed", "true");
    await expect.element(page.getByText("412 tokens")).toBeVisible();
    await page.getByRole("button", { name: "Dislike answer" }).click();
    await expect.element(like).toHaveAttribute("aria-pressed", "false");
    const call = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith("/feedback"));
    expect(String(call?.[0])).toContain("/threads/t-past/messages/m-assistant/feedback");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ feedback: "dislike" });
    const question = page.getByText("Omzet per SKU 3 bulan ke belakang").element();
    expect(getComputedStyle(question).color).toBe("rgb(255, 255, 255)");
    document.documentElement.classList.add("dark");
    try {
      expect(getComputedStyle(question).color).toBe("rgb(255, 255, 255)");
    } finally {
      document.documentElement.classList.remove("dark");
    }
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

  it("does not reopen the previous thread while New chat waits for the URL", async () => {
    renderStudioWithStaleThreadAfterNewChat();
    await expect
      .element(page.getByText(/Omzet tertinggi ada di/))
      .toBeVisible();

    await page.getByRole("button", { name: "Start fresh" }).click();

    await expect
      .element(page.getByText("What insights can I help with?"))
      .toBeVisible();
    await new Promise((resolve) => window.setTimeout(resolve, 50));
    await expect
      .element(page.getByText(/Omzet tertinggi ada di/))
      .not.toBeInTheDocument();

    await page.getByRole("button", { name: "Top SKUs last quarter" }).click();

    const messageUrls = vi
      .mocked(globalThis.fetch)
      .mock.calls.map(([input]) => String(input))
      .filter((url) => url.includes("/messages"));
    expect(
      messageUrls.some((url) => url.includes("/threads/t1/messages")),
    ).toBe(true);
    expect(
      messageUrls.some((url) => url.includes("/threads/t-past/messages")),
    ).toBe(false);
    await expect.element(page.getByText("Revenue per SKU")).toBeVisible();
  });
});
