import { describe, expect, it } from "vitest";
import { render } from "vitest-browser-react";
import { parseAssistantEvent } from "./events";
import { MessageList } from "./message-list";
import { restoredMessage, type TranscriptMessage } from "./use-assistant-transcript";

const answer = (source?: TranscriptMessage["source"]): TranscriptMessage => ({
  message_id: "a1",
  role: "assistant",
  content: "Net revenue was Rp 1,2 miliar.",
  tool_call: null,
  created_at: "2026-11-10T00:00:00Z",
  turn_state: "done",
  ...(source ? { source } : {}),
});

describe("answer source", () => {
  it("reads the source from the done frame and ignores an unknown one", () => {
    const done = (source: unknown) =>
      parseAssistantEvent("done", JSON.stringify({ message_id: "m", finish_reason: "stop", source }));
    expect(done({ kind: "semantic_view", trusted: true })).toMatchObject({
      source: { kind: "semantic_view", trusted: true },
    });
    expect(done({ kind: "rumour", trusted: true })).toMatchObject({ source: undefined });
  });

  it("comes back with a stored answer", () => {
    const restored = restoredMessage({
      message_id: "a1", role: "assistant", content: "x", created_at: "2026-11-10T00:00:00Z",
      steps: [{ kind: "answer", source: { kind: "query", trusted: false } }],
    });
    expect(restored.source).toEqual({ kind: "query", trusted: false });
  });

  it("says Trusted only for a governed source", async () => {
    const trusted = await render(
      <MessageList messages={[answer({ kind: "glossary", trusted: true })]} />,
    );
    await expect.element(trusted.getByTestId("answer-source"))
      .toHaveTextContent("Trusted·From the business glossary");
    trusted.unmount();
    const plain = await render(
      <MessageList messages={[answer({ kind: "query", trusted: false })]} />,
    );
    await expect.element(plain.getByTestId("answer-source"))
      .toHaveTextContent("From a query on your data");
    expect(plain.container.textContent).not.toContain("Trusted");
  });

  it("labels nothing when the answer used no source", async () => {
    const screen = await render(<MessageList messages={[answer()]} />);
    expect(screen.container.querySelector('[data-testid="answer-source"]')).toBeNull();
  });
});
