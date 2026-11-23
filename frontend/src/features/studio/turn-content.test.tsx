import { emptyRunState } from "./runtime/turn-reducer";
import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import type { AssistantEvent, TableBlock } from "@/features/assistant/types";
import { type OrderedContent, type TranscriptTurn } from "./studio-chat";
import { applyStudioEvent, replayThread, settleStream } from "./studio-transcript";
import { liveEncoder } from "./runtime/v2-test-encoder";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TurnContent } from "./turn-content";

const dataviz = vi.hoisted(() => ({ check: vi.fn() }));
vi.mock("./dataviz/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./dataviz/api")>()),
  datavizApi: dataviz,
}));
const withQueries = (ui: React.ReactNode) => (
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
);

const table: TableBlock = {
  title: "Channels in 2025",
  columns: [
    "sales_channel",
    "recognized_revenue",
    "gross_margin_pct",
    "order_count",
  ],
  rows: [
    ["Store", "3013024030096.50", "0.36952650", 158569],
    ["Mobile App", "3368049065451.00", "0.36962540", 177450],
  ],
};
const heading =
  "| sales_channel | recognized_revenue | gross_margin_pct | order_count |\n| --- | --- | --- | --- |\n";
const answers = {
  id:
    "Mobile App tertinggi.\n\n" +
    heading +
    "| Store | 3.013.024.030.096,50 | 36,95% | 158.569 |\n" +
    "| Mobile App | 3.368.049.065.451,00 | 36,96% | 177.450 |",
  en:
    "Mobile App is highest.\n\n" +
    heading +
    "| Store | 3,013,024,030,096.50 | 36.95% | 158,569 |\n" +
    "| Mobile App | 3,368,049,065,451.00 | 36.96% | 177,450 |",
};

function turn(answer: string, result = table): TranscriptTurn {
  return {
    id: "turn",
    question: "Compare channels in 2025",
    steps: [],
    answer,
    content: [
      { id: "answer", index: 0, type: "text", text: answer, complete: true },
      { id: "result", index: 1, type: "table", block: result, complete: true },
    ],
    blocks: { tables: [], charts: [], citations: [] },
    state: "done",
    run: emptyRunState("done"),
    protocolVersion: 1,
  };
}

const PAGE = {
  catalog_version: "nova-layout-1",
  fallback: "Useful fallback.",
  state_defaults: {},
  provenance: { evidence_ids: [], scope: "verified_result_rows", composer: "model" as const },
};

describe("one table per query result", () => {
  it("folds a table under the chart drawn from the same query", async () => {
    const original = turn("Revenue by month.", { ...table, tool_call_id: "call-1" });
    original.content.push({
      id: "chart", index: 2, type: "chart", complete: true,
      block: { tool_call_id: "call-1", chart_spec: '{"title":"Revenue by month","mark":"bar"}' },
    });
    const screen = await render(withQueries(<TurnContent turn={original} />));
    await expect.element(screen.getByText("Source data (1 table)", { exact: true })).toBeVisible();
    expect(screen.container.querySelector("details")?.open).toBe(false);
  });

  it("folds a table under a chart tool that drew its result", async () => {
    const original = turn("Revenue by month.", { ...table, tool_call_id: "call-1" });
    original.content.push({
      id: "chart", index: 2, type: "chart", complete: true,
      block: {
        tool_call_id: "chart-call", source_tool_call_id: "call-1",
        chart_spec: '{"title":"Revenue by month","mark":"bar"}',
      },
    });
    const screen = await render(withQueries(<TurnContent turn={original} />));
    await expect.element(screen.getByText("Source data (1 table)", { exact: true })).toBeVisible();
  });

  it("keeps a table of a different query in view beside a chart", async () => {
    const original = turn("Revenue by month.", { ...table, tool_call_id: "call-1" });
    original.content.push({
      id: "chart", index: 2, type: "chart", complete: true,
      block: { tool_call_id: "call-2", chart_spec: '{"title":"Orders","mark":"bar"}' },
    });
    const screen = await render(withQueries(<TurnContent turn={original} />));
    await expect.element(screen.getByRole("textbox", { name: "Filter rows" })).toBeVisible();
    // The chart's title renders, so its absence in the next case is meaningful.
    await expect.element(screen.getByText("Orders", { exact: true })).toBeVisible();
    expect(screen.getByText("Source data (1 table)", { exact: true }).query()).toBeNull();
  });

  it("draws a page answer with its title and figures", async () => {
    const original = turn("Revenue by month.", { ...table, tool_call_id: "call-1" });
    original.content.push({
      id: "ui", index: 2, type: "ui", complete: true,
      block: {
        ...PAGE,
        program: 'root = Page([h, k])\nh = Header("Revenue overview", "June and July")\n'
          + 'k = KpiTiles([Metric("Revenue", 1500, "number", 0.25, "vs. June")])',
      },
    });
    const screen = await render(withQueries(<TurnContent turn={original} />));
    await expect.element(screen.getByRole("heading", { name: "Revenue overview" })).toBeVisible();
    await expect.element(screen.getByText("vs. June")).toBeVisible();
  });

  it.each([
    ["done", true],
    ["error", false],
  ] as const)("keeps source tables accessible when the turn is %s", async (state, folded) => {
    const original = turn("Retail leads.");
    original.state = state;
    original.content.push({
      id: "ui", index: 2, type: "ui", complete: true,
      block: { ...PAGE, program: 'root = Page([t])\nt = Text("Interactive summary")' },
    });
    original.messageId = "message-1";
    dataviz.check.mockResolvedValue({ results: [
      { preview_ref: "pv1.a.b", status: "SUPPORTED", reason_code: "OK", message: "", can_add: true },
    ] });
    const screen = await render(withQueries(<TurnContent turn={original} datavizThreadId="thread-1" />));
    const source = screen.getByText("Source data (1 table)", { exact: true });
    if (folded) {
      expect(screen.container.querySelector("details")?.open).toBe(false);
      await userEvent.click(source);
    } else {
      await expect.element(source).not.toBeInTheDocument();
    }
    await expect.element(screen.getByRole("textbox", { name: "Filter rows" })).toBeVisible();
    if (state === "done") {
      await expect.element(screen.getByRole("button", { name: "Add to Dataviz" })).toBeVisible();
      expect(dataviz.check.mock.calls[0][0]).toEqual([
        { thread_id: "thread-1", message_id: "message-1", content_id: "result" },
      ]);
    } else {
      // A failed turn's results are never offered for publication.
      expect(screen.getByRole("button", { name: "Add to Dataviz" }).query()).toBeNull();
    }
  });

  it("draws an interactive answer whose close frame was lost once the run ends", async () => {
    const start = turn("Retail leads.");
    start.state = "streaming";
    start.content = [];
    const events = [
      // Streamed events pass the stream parser, which requires a real digest.
      { type: "ui", content_index: 0, content_id: "run-1:0", payload: {
        ...PAGE, fallback: "Retail is ahead of web.",
      } },
      { type: "ui_delta", content_index: 0, content_id: "run-1:0",
        text: 'root = Page([t])\nt = Text("Interactive summary")\n' },
      { type: "done", message_id: "", finish_reason: "stop" },
    ] satisfies AssistantEvent[];
    const feed = liveEncoder();
    let turns: TranscriptTurn[] = [{ ...start, run: emptyRunState(), protocolVersion: 2 }];
    for (const event of events)
      for (const v2 of feed(event)) turns = applyStudioEvent(turns, start.id, v2);
    const ended = settleStream(turns, start.id, false)[0];
    const screen = await render(withQueries(<TurnContent turn={ended} />));
    await expect.element(screen.getByText("Interactive summary")).toBeVisible();
    expect(screen.getByText("Retail is ahead of web.").query()).toBeNull();
  });

  it("explains an interactive answer that was too large to show", async () => {
    const message = turn("Retail leads.");
    message.content.push({
      id: "ui", index: 2, type: "ui", complete: true, tooLarge: true,
      block: { ...PAGE, program: "", fallback: "Retail is ahead of web." },
    });
    const screen = await render(withQueries(<TurnContent turn={message} />));
    await expect.element(screen.getByText(
      "This interactive answer is too large to show. Here is the written answer.",
    )).toBeVisible();
    await expect.element(screen.getByText("Retail is ahead of web.")).toBeVisible();
    expect(screen.getByRole("region", { name: "Interactive answer" }).query()).toBeNull();
  });

  it.each(["ordered", "legacy"])(
    "keeps %s fallback media offline without hiding its labels",
    async (mode) => {
      const answer =
        "Useful fallback. ![Private image](https://business.invalid/image) " +
        "[Reference](https://business.invalid/?form=private)";
      const message = turn(answer);
      if (mode === "legacy") message.content = [];
      const screen = await render(<TurnContent turn={message} />);
      expect(screen.container.querySelectorAll("img, a")).toHaveLength(0);
      await expect.element(screen.getByText("Private image")).toBeVisible();
      await expect.element(screen.getByText("Reference")).toBeVisible();
    },
  );
  for (const [locale, answer] of Object.entries(answers)) {
    it(`keeps the summary and one interactive table for a ${locale} comparison`, async () => {
      const original = turn(answer);
      const screen = await render(<TurnContent turn={original} />);
      expect(screen.container.querySelectorAll("table")).toHaveLength(1);
      await expect
        .element(screen.getByText(answer.split("\n")[0]))
        .toBeVisible();
      await screen.getByRole("textbox", { name: "Filter rows" }).fill("Store");
      await expect.element(screen.getByText("1 / 2 rows")).toBeVisible();
      await screen.getByRole("textbox", { name: "Filter rows" }).fill("");
      await screen
        .getByRole("button", { name: "Order count", exact: true })
        .click();
      expect(
        screen.container.querySelector('[aria-sort="ascending"]'),
      ).not.toBeNull();
      // Without a stored thread there is nothing to verify, so no action shows.
      expect(screen.getByRole("button", { name: "Add to Dataviz" }).query()).toBeNull();
      await expect
        .element(
          screen.getByRole("button", { name: "Download results as CSV" }),
        )
        .toBeVisible();
      expect(original.answer).toBe(answer);
      expect(original.content[1]).toEqual(
        expect.objectContaining({ block: table }),
      );
    });
  }

  it("applies the same rendering to a saved conversation", async () => {
    const [saved] = replayThread([
      {
        message_id: "u",
        role: "user",
        content: "Compare channels",
        created_at: "2026-09-25T00:00:00",
      },
      {
        message_id: "a",
        role: "assistant",
        content: answers.id,
        created_at: "2026-09-25T00:00:01",
        steps: [{ kind: "table", ...table }, { kind: "answer" }],
      },
    ]);
    const screen = await render(<TurnContent turn={saved} />);
    expect(screen.container.querySelectorAll("table")).toHaveLength(1);
    await expect
      .element(screen.getByRole("textbox", { name: "Filter rows" }))
      .toBeVisible();
  });

  it("matches reordered rows and columns with formatted cell content", async () => {
    const answer =
      "| order_count | sales_channel | gross_margin_pct | recognized_revenue |\n| --- | --- | --- | --- |\n" +
      "| 177,450 | **Mobile App** | 36.96% | 3,368,049,065,451.00 |\n" +
      "| 158,569 | Store | 36.95% | 3,013,024,030,096.50 |";
    const screen = await render(<TurnContent turn={turn(answer)} />);
    expect(screen.container.querySelectorAll("table")).toHaveLength(1);
  });

  for (const [label, answer] of [
    ["different values", answers.en.replace("158,569", "158,570")],
    [
      "different columns",
      answers.en.replace("recognized_revenue", "gross_profit"),
    ],
    ["a subset of rows", answers.en.split("\n").slice(0, -1).join("\n")],
    [
      "duplicate rows",
      answers.en.replace(
        /\| Mobile App \|[^\n]+/,
        answers.en.split("\n").slice(-2, -1).join(""),
      ),
    ],
  ]) {
    it(`preserves a Markdown table with ${label}`, async () => {
      const screen = await render(<TurnContent turn={turn(answer)} />);
      expect(screen.container.querySelectorAll("table")).toHaveLength(2);
    });
  }

  it("keeps Markdown until the matching result is visible in the stream", async () => {
    const pending = turn(answers.en);
    pending.state = "streaming";
    pending.content[0].complete = false;
    const screen = await render(<TurnContent turn={pending} running />);
    // Text watched as it streams is revealed progressively; wait for all of it.
    await expect.poll(() => screen.container.querySelector("[data-revealing]")).toBeNull();
    expect(screen.container.querySelectorAll("table")).toHaveLength(1);
    await expect
      .element(screen.getByRole("textbox", { name: "Filter rows" }))
      .not.toBeInTheDocument();
    const complete: OrderedContent[] = pending.content.map((item) => ({
      ...item,
      complete: true,
    }));
    await screen.rerender(
      <TurnContent turn={{ ...pending, content: complete, state: "done" }} />,
    );
    expect(screen.container.querySelectorAll("table")).toHaveLength(1);
    await expect
      .element(screen.getByRole("textbox", { name: "Filter rows" }))
      .toBeVisible();
  });

  it("keeps a standalone answer that has no interactive result", async () => {
    const standalone = turn(answers.en);
    standalone.content = [];
    const screen = await render(<TurnContent turn={standalone} />);
    expect(screen.container.querySelectorAll("table")).toHaveLength(1);
    await expect
      .element(screen.getByText("158,569", { exact: true }))
      .toBeVisible();
  });
});
