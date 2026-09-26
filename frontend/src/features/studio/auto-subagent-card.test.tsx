import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { page, userEvent } from "vitest/browser";
import { afterEach, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { agentsApi, type Agent, type AutoChildTimeline, type AutoRun } from "@/features/agents/api";
import { AutoSubagentCard, AutoSubagentPanel } from "./auto-subagent-card";
import { latestParticipants } from "./smart-agent-tree";
import { AutoTurnRail } from "./auto-turn-rail";
import "@/styles/index.css";

const runs = [
  { run_id: "root", agent_id: "__auto__", depth: 0, status: "waiting_for_agent", objective: "Why did revenue fall?" },
  { run_id: "finance", agent_id: "finance", depth: 1, status: "completed", objective: "Quantify revenue", summary: "Revenue fell 22%", prompt_tokens: 100, completion_tokens: 50 },
  { run_id: "marketing", agent_id: "marketing", depth: 1, status: "running", objective: "Check campaign attribution", summary: null, prompt_tokens: null, completion_tokens: null },
] as AutoRun[];
const agents = [{ agent_id: "finance", name: "Finance" }, { agent_id: "marketing", name: "Marketing" }] as Agent[];

const smartRuns = [
  { ...runs[0], agent_id: "__smart__", agent_session_id: "root", agent_path: "/root" },
  { ...runs[1], agent_session_id: "finance", parent_agent_session_id: "root", agent_path: "/root/finance", turn_number: 1 },
  { ...runs[2], agent_session_id: "marketing", parent_agent_session_id: "root", agent_path: "/root/marketing", turn_number: 1 },
  { ...runs[2], run_id: "cohort", agent_id: "cohort", agent_name: "Customer Analytics", agent_session_id: "cohort", parent_agent_session_id: "marketing", depth: 2, agent_path: "/root/marketing/cohort", turn_number: 1 },
] as AutoRun[];

const finishedTimeline: AutoChildTimeline = {
  run: { run_id: "finance", root_run_id: "root", agent_id: "finance", agent_name: "Finance", objective: "Quantify revenue", status: "completed", result_summary: "Revenue fell 22%", error_class: null, prompt_tokens: 100, completion_tokens: 50 },
  next_cursor: 7, has_more: false,
  events: [
    { event_id: 1, run_id: "finance", type: "agent_started", payload: {}, created_at: "2026-09-25T00:00:00" },
    { event_id: 2, run_id: "root", type: "agent_message", payload: { sender_run_id: "root", recipient_run_id: "finance", origin: "agent", content: "Check the channel totals" }, created_at: "2026-09-25T00:00:01" },
    { event_id: 3, run_id: "finance", type: "child_activity", payload: { event_type: "plan", steps: [{ id: "a", text: "Query channel totals", status: "done" }] }, created_at: "2026-09-25T00:00:02" },
    { event_id: 4, run_id: "finance", type: "child_activity", payload: { event_type: "tool_call", tool_call_id: "tool-a", tool_name: "semantic_query", status: "running", sql_preview: "SELECT revenue FROM sales" }, created_at: "2026-09-25T00:00:03" },
    { event_id: 5, run_id: "finance", type: "child_activity", payload: { event_type: "tool_status", tool_call_id: "tool-a", status: "done" }, created_at: "2026-09-25T00:00:04" },
    { event_id: 6, run_id: "finance", type: "agent_message", payload: { sender_run_id: "finance", recipient_run_id: "root", origin: "agent", content: "The query returned five channels" }, created_at: "2026-09-25T00:00:05" },
    { event_id: 7, run_id: "finance", type: "child_activity", payload: { event_type: "answer", text: "Revenue fell **22%**." }, created_at: "2026-09-25T00:00:06" },
    { event_id: 8, run_id: "finance", type: "agent_completed", payload: { summary: "Revenue fell **22%**." }, created_at: "2026-09-25T00:00:07" },
  ],
};

function Fixture({ data = runs, catalog = agents, loading = false, error = false, retry = () => {} }: { data?: AutoRun[]; catalog?: Agent[]; loading?: boolean; error?: boolean; retry?: () => void }) {
  const [selectedChild, setSelectedChild] = useState<{ rootRunId: string; childRunId: string } | null>(null);
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <div className="relative flex h-[700px] min-w-0 overflow-hidden bg-background">
      <div data-testid="main-chat-column" className={selectedChild ? "relative hidden min-h-0 min-w-0 flex-1 flex-col lg:flex" : "relative flex min-h-0 min-w-0 flex-1 flex-col"}>
        {!selectedChild ? <AutoSubagentCard runs={data} agents={catalog} loading={loading} error={error} retry={retry} onSelectChild={(childRunId) => setSelectedChild({ rootRunId: "root", childRunId })} /> : null}
        <button type="button" className="self-end" onClick={() => {}}>Main composer</button>
      </div>
      {selectedChild ? <AutoSubagentPanel selected={selectedChild} runs={data} agents={catalog} onClose={() => setSelectedChild(null)} onRefreshTree={retry} /> : null}
    </div>
  </QueryClientProvider>;
}

afterEach(async () => {
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
  await page.viewport(1440, 900);
});

it("renders a nested participant and opens its own timeline", async () => {
  const timeline = vi.spyOn(agentsApi, "getAutoChildTimeline").mockResolvedValue({ ...finishedTimeline, run: { ...finishedTimeline.run, run_id: "cohort", agent_id: "cohort", agent_name: "Customer Analytics" } });
  render(<Fixture data={smartRuns} />);
  await page.getByRole("button", { name: "Open Customer Analytics conversation" }).click();
  await expect.element(page.getByRole("heading", { name: "Customer Analytics" })).toBeVisible();
  expect(timeline).toHaveBeenCalledWith("root", "cohort", -1);
});

it("groups follow-up turns under the same participant", () => {
  const next = { ...smartRuns[1], run_id: "finance-turn-2", turn_number: 2, status: "running" };
  const participants = latestParticipants([...smartRuns, next]);
  expect(participants).toHaveLength(4);
  expect(participants.find((run) => run.agent_session_id === "finance")?.run_id).toBe("finance-turn-2");
});

it("keeps the active turn visible while its follow-up waits", () => {
  const active = { ...smartRuns[1], status: "running" };
  const queued = { ...active, run_id: "finance-turn-2", turn_number: 2, status: "waiting_for_turn" };
  expect(latestParticipants([active, queued])[0].run_id).toBe("finance");
});

it("steers Smart itself without offering a self-interrupt", async () => {
  vi.spyOn(agentsApi, "getAutoChildTimeline").mockResolvedValue({ ...finishedTimeline, run: { ...finishedTimeline.run, run_id: "root", agent_id: "__smart__", agent_name: "Smart", status: "running" } });
  const send = vi.spyOn(agentsApi, "sendAutoChildMessage").mockResolvedValue({ message_id: "steering", status: "queued" });
  render(<Fixture data={smartRuns} />);
  await page.getByRole("button", { name: "Open Smart conversation" }).click();
  await page.getByRole("textbox", { name: "Message subagent" }).fill("Prioritize enterprise customers");
  await page.getByRole("button", { name: "Send to subagent" }).click();
  expect(send).toHaveBeenCalledWith("root", "root", expect.objectContaining({ content: "Prioritize enterprise customers" }));
  await expect.element(page.getByRole("button", { name: "Interrupt", exact: true })).not.toBeInTheDocument();
});

it("starts a follow-up for an idle Smart participant", async () => {
  vi.spyOn(agentsApi, "getAutoChildTimeline").mockResolvedValue(finishedTimeline);
  const followup = vi.spyOn(agentsApi, "followupSmartAgent").mockResolvedValue({});
  render(<Fixture data={smartRuns} />);
  await page.getByRole("button", { name: "Open Finance conversation" }).click();
  await page.getByRole("textbox", { name: "Message subagent" }).fill("Correlate the campaign evidence");
  await page.getByRole("button", { name: "Start follow-up" }).click();
  expect(followup).toHaveBeenCalledWith("root", "finance", "Correlate the campaign evidence", expect.any(String));
});

it("interrupts only the selected nested participant", async () => {
  vi.spyOn(agentsApi, "getAutoChildTimeline").mockResolvedValue({ ...finishedTimeline, run: { ...finishedTimeline.run, run_id: "cohort", agent_name: "Customer Analytics", status: "running" } });
  const interrupt = vi.spyOn(agentsApi, "interruptSmartAgent").mockResolvedValue({});
  render(<Fixture data={smartRuns} />);
  await page.getByRole("button", { name: "Open Customer Analytics conversation" }).click();
  await page.getByRole("button", { name: "Interrupt", exact: true }).click();
  expect(interrupt).toHaveBeenCalledWith("root", "cohort");
});

for (const width of [320, 1440]) {
  for (const dark of [false, true]) {
    it(`keeps a long tree inside the viewport at ${width}px in ${dark ? "dark" : "light"} mode`, async () => {
      await page.viewport(width, 900);
      document.documentElement.classList.toggle("dark", dark);
      const many = Array.from({ length: 25 }, (_, index) => ({ ...smartRuns[1], run_id: `agent-${index}`, agent_session_id: `agent-${index}`, agent_name: `Specialist ${index}` }));
      render(<Fixture data={[smartRuns[0], ...many]} />);
      const region = page.getByRole("region", { name: "Smart agent activity" });
      await expect.element(region).toBeVisible();
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
      expect(region.element().getBoundingClientRect().bottom).toBeLessThanOrEqual(900);
      expect(region.element().getBoundingClientRect().height).toBeLessThanOrEqual(700 / 3 + 1);
      await expect.element(page.getByRole("button", { name: "Main composer" })).toBeVisible();
      await page.screenshot({ path: `__screenshots__/smart-tree-${dark ? "dark" : "light"}-${width}.png` });
    });
  }
}

it("uses the name saved on the child run when the catalog cannot load", async () => {
  vi.spyOn(agentsApi, "getAutoChildTimeline").mockResolvedValue(finishedTimeline);
  render(<Fixture data={[runs[0], { ...runs[1], agent_name: "Finance" }]} catalog={[]} />);
  await expect.element(page.getByRole("button", { name: "Open Finance conversation" })).toBeVisible();
  await page.getByRole("button", { name: "Open Finance conversation" }).click();
  await expect.element(page.getByRole("complementary", { name: /Subagent conversation: Finance/ })).toBeVisible();
});

it("opens the specialist's complete replay with both directions of agent messages", async () => {
  await page.viewport(1440, 900);
  document.documentElement.classList.add("dark");
  vi.spyOn(agentsApi, "getAutoChildTimeline").mockResolvedValue(finishedTimeline);
  render(<Fixture />);
  const card = page.getByRole("region", { name: "Smart agent activity" });
  await expect.element(card).toBeVisible();
  await expect.element(card.getByText("1 working · 1 done")).toBeVisible();
  await card.getByRole("button", { name: "Open Finance conversation" }).click();
  const panel = page.getByRole("complementary", { name: /Subagent conversation/ });
  await expect.element(panel).toBeVisible();
  expect(panel.element().getBoundingClientRect().width).toBeGreaterThan(500);
  await expect.element(panel.getByText("Assigned task")).toBeVisible();
  await expect.element(panel.getByText("Smart to Finance")).toBeVisible();
  await expect.element(panel.getByText("finance to root")).toBeVisible();
  await expect.element(panel.getByText("Query channel totals")).toBeVisible();
  await expect.element(panel.getByText("SELECT revenue FROM sales")).toBeVisible();
  expect(panel.element().textContent?.match(/Revenue fell 22%/g)?.length).toBe(1);
  await expect.element(panel.getByText("read only", { exact: false })).toBeVisible();
  const mainComposer = page.getByRole("button", { name: "Main composer" });
  await expect.element(mainComposer).toBeVisible();
  await mainComposer.click();
  expect(document.activeElement).toBe(mainComposer.element());
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  await panel.getByRole("button", { name: "Back to conversation" }).click();
  await expect.element(page.getByTestId("subagent-panel")).not.toBeInTheDocument();
  await expect.element(card).toBeVisible();
  await expect.element(page.getByRole("heading", { name: "All subagents" })).not.toBeInTheDocument();
  await card.getByRole("button", { name: "Open Finance conversation" }).click();
  await expect.element(panel.getByRole("heading", { name: "Finance" })).toBeVisible();
  await userEvent.keyboard("{Escape}");
  await expect.element(page.getByTestId("subagent-panel")).not.toBeInTheDocument();
});

it("marks the child's persisted activity limit instead of implying a complete trace", async () => {
  vi.spyOn(agentsApi, "getAutoChildTimeline").mockResolvedValue({
    ...finishedTimeline,
    events: [...finishedTimeline.events, { event_id: 9, run_id: "finance", type: "child_activity", payload: { event_type: "omitted", reason: "activity_limit" }, created_at: "2026-09-25T00:00:08" }],
  });
  render(<Fixture />);
  await page.getByRole("button", { name: "Open Finance conversation" }).click();
  const panel = page.getByRole("complementary", { name: /Subagent conversation/ });
  await expect.element(panel.getByRole("status")).toHaveTextContent("Additional activity was omitted after this run reached its event limit.");
});

it("catches up when completed status appears before the child's final answer event", async () => {
  const started = finishedTimeline.events[0];
  const answer = finishedTimeline.events[6];
  const terminal = finishedTimeline.events[7];
  let initialRead = true;
  const timeline = vi.spyOn(agentsApi, "getAutoChildTimeline").mockImplementation(async (_root, _child, after) => {
    if (after === -1 && initialRead) {
      initialRead = false;
      return { ...finishedTimeline, run: { ...finishedTimeline.run, status: "running" }, events: [started], next_cursor: 1 };
    }
    if (after === 1) return { ...finishedTimeline, events: [terminal], next_cursor: 8 };
    return { ...finishedTimeline, events: [started, answer, terminal], next_cursor: 8 };
  });
  render(<Fixture />);
  await page.getByRole("button", { name: "Open Finance conversation" }).click();
  const panel = page.getByRole("complementary", { name: /Subagent conversation/ });
  await expect.element(panel.getByText("Finance started working.")).toBeVisible();
  await expect.poll(() => timeline.mock.calls.length, { timeout: 5000 }).toBeGreaterThanOrEqual(3);
  await expect.element(panel.getByText("Revenue fell 22%.")).toBeVisible();
  expect(timeline.mock.calls.map((call) => call[2])).toEqual([-1, 1, -1]);
  expect(panel.element().textContent?.match(/Revenue fell 22%/g)?.length).toBe(1);
});

it("lets the user steer and cancel a working subagent", async () => {
  await page.viewport(1440, 900);
  vi.spyOn(agentsApi, "getAutoChildTimeline").mockResolvedValue({ ...finishedTimeline, run: { ...finishedTimeline.run, run_id: "marketing", agent_id: "marketing", agent_name: "Marketing", status: "running", objective: "Check campaign attribution" }, events: [] });
  const send = vi.spyOn(agentsApi, "sendAutoChildMessage").mockResolvedValue({ message_id: "m1", status: "queued" });
  const cancel = vi.spyOn(agentsApi, "cancelAutoChild").mockResolvedValue({ status: "cancelled" });
  render(<Fixture />);
  await page.getByRole("button", { name: "Open Marketing conversation" }).click();
  const panel = page.getByRole("complementary", { name: /Subagent conversation/ });
  await expect.element(panel).toBeVisible();
  await panel.getByRole("textbox", { name: "Message subagent" }).fill("Check paid channels too");
  await panel.getByRole("button", { name: "Send to subagent" }).click();
  await expect.poll(() => send.mock.calls.length).toBe(1);
  expect(send.mock.calls[0][2].content).toBe("Check paid channels too");
  await panel.getByRole("button", { name: "Cancel agent" }).click();
  await expect.poll(() => cancel.mock.calls.length).toBe(1);
  expect(cancel).toHaveBeenCalledWith("root", "marketing");
});

it("reflows the card and split panel at narrow widths without horizontal overflow", async () => {
  await page.viewport(320, 750);
  vi.spyOn(agentsApi, "getAutoChildTimeline").mockResolvedValue(finishedTimeline);
  render(<Fixture />);
  await expect.element(page.getByRole("button", { name: "Open Finance conversation" })).toBeVisible();
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
  await page.getByRole("button", { name: "Open Finance conversation" }).click();
  await expect.element(page.getByRole("complementary", { name: /Subagent conversation/ })).toBeVisible();
  expect(page.getByRole("complementary", { name: /Subagent conversation/ }).element().getBoundingClientRect().width).toBeGreaterThanOrEqual(310);
  expect(getComputedStyle(page.getByTestId("main-chat-column").element()).display).toBe("none");
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
  await userEvent.keyboard("{Escape}");
  await page.viewport(375, 800);
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(375);
});

it("shows loading and retry states without inventing activity", async () => {
  await page.viewport(1440, 900);
  const retry = vi.fn();
  const view = await render(<Fixture data={[]} loading retry={retry} />);
  await expect.element(page.getByText("Loading activity…").first()).toBeVisible();
  await view.rerender(<Fixture data={[]} error retry={retry} />);
  await expect.element(page.getByRole("alert")).toHaveTextContent("Activity unavailable.");
  await page.getByRole("button", { name: "Retry" }).click();
  expect(retry).toHaveBeenCalledOnce();
});

it("opens the subagent from the main chat's real spawn event", async () => {
  vi.spyOn(agentsApi, "getAutoRunEvents").mockResolvedValue({ events: [
    { event_id: 1, run_id: "root", type: "agent_started", payload: {}, created_at: "2026-09-25T00:00:00" },
    { event_id: 2, run_id: "finance", type: "agent_queued", payload: { agent_id: "finance", objective: "Quantify revenue" }, created_at: "2026-09-25T00:00:01" },
    { event_id: 3, run_id: "finance", type: "agent_completed", payload: {}, created_at: "2026-09-25T00:00:02" },
    { event_id: 4, run_id: "root", type: "agent_completed", payload: {}, created_at: "2026-09-25T00:00:03" },
  ] });
  const onOpenChild = vi.fn();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <AutoTurnRail rootRunId="root" agents={agents} running={false} onOpenChild={onOpenChild} />
  </QueryClientProvider>);
  const spawn = page.getByRole("button", { name: "Called Finance. Open subagent conversation" });
  await expect.element(spawn).toBeVisible();
  await spawn.click();
  expect(onOpenChild).toHaveBeenCalledWith("finance");
  await expect.element(page.getByText("Starting analysis…")).not.toBeInTheDocument();
});

it("recovers the child conversation after a timeline request fails", async () => {
  vi.spyOn(agentsApi, "getAutoChildTimeline")
    .mockRejectedValueOnce(new Error("temporarily unavailable"))
    .mockResolvedValue(finishedTimeline);
  render(<Fixture />);
  await page.getByRole("button", { name: "Open Finance conversation" }).click();
  const panel = page.getByRole("complementary", { name: /Subagent conversation/ });
  await expect.element(panel.getByRole("alert")).toHaveTextContent("Could not load the conversation.");
  await panel.getByRole("button", { name: "Retry" }).click();
  await expect.element(panel.getByText("Smart to Finance")).toBeVisible();
});
