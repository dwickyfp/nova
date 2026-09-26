import { afterEach, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  agentsApi,
  studioApi,
  type Agent,
  type AgentMessage,
} from "@/features/agents/api";
import { StudioChat } from "./studio-chat";
import "@/styles/index.css";

function contrast(element: HTMLElement): number {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d")!;
  context.fillStyle = "white";
  context.fillRect(0, 0, 1, 1);
  const ancestors: HTMLElement[] = [];
  for (let node: HTMLElement | null = element; node; node = node.parentElement)
    ancestors.unshift(node);
  for (const node of ancestors) {
    context.fillStyle = getComputedStyle(node).backgroundColor;
    context.fillRect(0, 0, 1, 1);
  }
  const luminance = () =>
    Array.from(context.getImageData(0, 0, 1, 1).data)
      .slice(0, 3)
      .map((channel) => channel / 255)
      .map((channel) =>
        channel <= 0.04045
          ? channel / 12.92
          : ((channel + 0.055) / 1.055) ** 2.4,
      )
      .reduce(
        (sum, channel, index) =>
          sum + channel * [0.2126, 0.7152, 0.0722][index],
        0,
      );
  const background = luminance();
  context.fillStyle = getComputedStyle(element).color;
  context.fillRect(0, 0, 1, 1);
  const foreground = luminance();
  return (
    (Math.max(foreground, background) + 0.05) /
    (Math.min(foreground, background) + 0.05)
  );
}

afterEach(async () => {
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
  await page.viewport(1280, 720);
});

it.each([
  [320, false],
  [320, true],
  [1280, false],
  [1280, true],
] as const)("loads older turns at %i px (dark: %s)", async (width, dark) => {
  await page.viewport(width, 720);
  document.documentElement.classList.toggle("dark", dark);
  const agent = {
    agent_id: "history-test",
    name: "History",
    sample_questions: [],
  } as unknown as Agent;
  const message = (
    id: string,
    role: AgentMessage["role"],
    content: string,
  ): AgentMessage => ({
    message_id: id,
    role,
    content,
    created_at: "2026-09-25T00:00:00Z",
    steps: [],
  });
  const newest = [
    message("boundary-answer", "assistant", "Answer across the page boundary"),
  ];
  for (let i = 0; i < 24; i++) {
    newest.push(message(`q-${i}`, "user", `Question ${i}`));
    newest.push(message(`a-${i}`, "assistant", `Response ${i}. `.repeat(30)));
  }
  const older = [
    message("old-q", "user", "Earlier conversation"),
    message("old-a", "assistant", "Earlier response"),
    message("boundary-question", "user", "Question across the page boundary"),
  ];
  vi.spyOn(studioApi, "settings").mockResolvedValue({
    preferences: {},
  } as never);
  const getThread = vi
    .spyOn(agentsApi, "getThread")
    .mockImplementation(async (_agent, threadId, cursor) => ({
      thread: { thread_id: threadId } as never,
      messages: cursor ? older : newest,
      next_cursor: cursor ? null : "older-page",
    }));
  const view = await render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <div className="flex h-svh flex-col overflow-hidden">
        <header className="shrink-0 p-2">History test</header>
        <StudioChat
          agent={agent}
          agents={[agent]}
          onSelectAgent={() => {}}
          currentRole={null}
          activeThreadId="history-thread"
          onThreadChange={() => {}}
          newChatNonce={0}
        />
      </div>
    </QueryClientProvider>,
  );
  await expect
    .element(view.getByText("Answer across the page boundary", { exact: true }))
    .toBeInTheDocument();
  expect(getThread.mock.calls.some((call) => call[2])).toBe(false);
  const loadOlder = view.getByRole("button", { name: "Load older messages" });
  const button = loadOlder.element() as HTMLButtonElement;
  expect(contrast(button)).toBeGreaterThanOrEqual(4.5);
  if (width === 320)
    expect(button.getBoundingClientRect().height).toBeGreaterThanOrEqual(44);
  button.focus();
  expect(document.activeElement).toBe(button);
  expect(getComputedStyle(button).boxShadow).not.toBe("none");
  await userEvent.keyboard("{Enter}");
  await expect
    .element(
      view.getByText("Question across the page boundary", { exact: true }),
    )
    .toBeInTheDocument();
  await expect
    .element(view.getByText("Earlier conversation", { exact: true }))
    .toBeInTheDocument();
  expect(
    view.getByText("Answer across the page boundary", { exact: true }).all()
      .length,
  ).toBe(1);
  await expect
    .element(view.getByRole("button", { name: "Load older messages" }))
    .not.toBeInTheDocument();
  expect(getThread).toHaveBeenLastCalledWith(
    "history-test",
    "history-thread",
    "older-page",
  );
  const root = document.scrollingElement!;
  expect(root.scrollHeight).toBeLessThanOrEqual(window.innerHeight + 1);
  expect(root.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 1);
  await expect
    .element(view.getByText("History test", { exact: true }))
    .toBeVisible();
});
