import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import type { TableBlock } from "@/features/assistant/types";
import { replayThread, type OrderedContent, type TranscriptTurn } from "./studio-chat";
import { TurnContent } from "./turn-content";

const table: TableBlock = {
  title: "Channels in 2025",
  columns: ["sales_channel", "recognized_revenue", "gross_margin_pct", "order_count"],
  rows: [
    ["Store", "3013024030096.50", "0.36952650", 158569],
    ["Mobile App", "3368049065451.00", "0.36962540", 177450],
  ],
};
const heading = "| sales_channel | recognized_revenue | gross_margin_pct | order_count |\n| --- | --- | --- | --- |\n";
const answers = {
  id: "Mobile App tertinggi.\n\n" + heading
    + "| Store | 3.013.024.030.096,50 | 36,95% | 158.569 |\n"
    + "| Mobile App | 3.368.049.065.451,00 | 36,96% | 177.450 |",
  en: "Mobile App is highest.\n\n" + heading
    + "| Store | 3,013,024,030,096.50 | 36.95% | 158,569 |\n"
    + "| Mobile App | 3,368,049,065,451.00 | 36.96% | 177,450 |",
};

function turn(answer: string, result = table): TranscriptTurn {
  return {
    id: "turn", question: "Compare channels in 2025", steps: [], answer,
    content: [
      { id: "answer", index: 0, type: "text", text: answer, complete: true },
      { id: "result", index: 1, type: "table", block: result, complete: true },
    ],
    pendingConsent: null, blocks: { tables: [], charts: [], citations: [] }, state: "done",
  };
}

describe("one table per query result", () => {
  for (const [locale, answer] of Object.entries(answers)) {
    it(`keeps the summary and one interactive table for a ${locale} comparison`, async () => {
      const original = turn(answer);
      const onSave = vi.fn();
      const screen = await render(<TurnContent turn={original} onSaveArtifact={onSave} />);
      expect(screen.container.querySelectorAll("table")).toHaveLength(1);
      await expect.element(screen.getByText(answer.split("\n")[0])).toBeVisible();
      await screen.getByRole("textbox", { name: "Filter rows" }).fill("Store");
      await expect.element(screen.getByText("1 / 2 rows")).toBeVisible();
      await screen.getByRole("textbox", { name: "Filter rows" }).fill("");
      await screen.getByRole("button", { name: "order_count", exact: true }).click();
      expect(screen.container.querySelector('[aria-sort="ascending"]')).not.toBeNull();
      await userEvent.click(screen.getByRole("button", { name: "Save table as artifact" }));
      expect(onSave).toHaveBeenCalledWith(original.content[1]);
      await expect.element(screen.getByRole("button", { name: "Download results as CSV" })).toBeVisible();
      expect(original.answer).toBe(answer);
      expect(original.content[1]).toEqual(expect.objectContaining({ block: table }));
    });
  }

  it("applies the same rendering to a saved conversation", async () => {
    const [saved] = replayThread([
      { message_id: "u", role: "user", content: "Compare channels", created_at: "2026-09-25T00:00:00" },
      { message_id: "a", role: "assistant", content: answers.id, created_at: "2026-09-25T00:00:01", steps: [{ kind: "table", ...table }, { kind: "answer" }] },
    ]);
    const screen = await render(<TurnContent turn={saved} />);
    expect(screen.container.querySelectorAll("table")).toHaveLength(1);
    await expect.element(screen.getByRole("textbox", { name: "Filter rows" })).toBeVisible();
  });

  it("matches reordered rows and columns with formatted cell content", async () => {
    const answer = "| order_count | sales_channel | gross_margin_pct | recognized_revenue |\n| --- | --- | --- | --- |\n"
      + "| 177,450 | **Mobile App** | 36.96% | 3,368,049,065,451.00 |\n"
      + "| 158,569 | Store | 36.95% | 3,013,024,030,096.50 |";
    const screen = await render(<TurnContent turn={turn(answer)} />);
    expect(screen.container.querySelectorAll("table")).toHaveLength(1);
  });

  for (const [label, answer] of [
    ["different values", answers.en.replace("158,569", "158,570")],
    ["different columns", answers.en.replace("recognized_revenue", "gross_profit")],
    ["a subset of rows", answers.en.split("\n").slice(0, -1).join("\n")],
    ["duplicate rows", answers.en.replace(/\| Mobile App \|[^\n]+/, answers.en.split("\n").slice(-2, -1).join(""))],
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
    expect(screen.container.querySelectorAll("table")).toHaveLength(1);
    await expect.element(screen.getByRole("textbox", { name: "Filter rows" })).not.toBeInTheDocument();
    const complete: OrderedContent[] = pending.content.map((item) => ({ ...item, complete: true }));
    await screen.rerender(<TurnContent turn={{ ...pending, content: complete, state: "done" }} />);
    expect(screen.container.querySelectorAll("table")).toHaveLength(1);
    await expect.element(screen.getByRole("textbox", { name: "Filter rows" })).toBeVisible();
  });

  it("keeps a standalone Smart answer that has no interactive result", async () => {
    const standalone = turn(answers.en);
    standalone.content = [];
    const screen = await render(<TurnContent turn={standalone} />);
    expect(screen.container.querySelectorAll("table")).toHaveLength(1);
    await expect.element(screen.getByText("158,569", { exact: true })).toBeVisible();
  });
});
