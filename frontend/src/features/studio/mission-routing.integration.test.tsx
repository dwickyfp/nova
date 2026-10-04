import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { agentsApi, studioApi, type Agent } from "@/features/agents/api";
import { StudioChat } from "./studio-chat";
import { workflowApi, type Mission } from "./workflow-api";
import "@/styles/index.css";

const mission: Mission = {
  mission_id: "revenue", thread_id: "routing-thread", objective: "Revenue decline", work_intent: "INVESTIGATE",
  status: "completed", revision: 3, run_ids: ["revenue-run"], stages: [], evidence_refs: [], object_refs: [],
  cancel_requested: false, created_at: "2026-10-04T00:00:00Z", updated_at: "2026-10-04T00:00:00Z",
};
const agent: Agent = {
  agent_id: "routing-agent", owner_name: "nova", database_name: null, schema_name: null, name: "Analyst",
  description: "Governed analysis", avatar: null, color: null, model_provider_id: null, model_name: null,
  instructions_response: "", instructions_orchestration: "", response_style: null, sample_questions: [],
  budget_seconds: null, budget_tokens: null, tool_not_accessible: "", default_tools: [], default_skills: [],
  policy: "auto_read_only", semantic_model_id: null, semantic_model_ids: [], visibility: "private",
  created_at: "2026-10-04T00:00:00Z", updated_at: "2026-10-04T00:00:00Z",
};
let client: QueryClient;
function accepted() {
  return new Response('event: done\ndata: {"message_id":"answer","finish_reason":"stop"}\n\n', {
    headers: { "Content-Type": "text/event-stream" },
  });
}
async function host() {
  await render(<QueryClientProvider client={client}><div className="flex h-dvh min-w-0 overflow-hidden">
    <StudioChat agent={agent} agents={[agent]} onSelectAgent={vi.fn()} currentRole="ANALYST" activeThreadId={mission.thread_id} onThreadChange={vi.fn()} />
  </div></QueryClientProvider>);
  await expect.element(page.getByRole("button", { name: "Continue mission", exact: true })).toBeEnabled();
}
beforeEach(async () => {
  await page.viewport(1280, 720);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  vi.spyOn(studioApi, "settings").mockResolvedValue({ preferences: {} } as never);
  vi.spyOn(agentsApi, "getThread").mockResolvedValue({ thread: { thread_id: mission.thread_id } as never, messages: [] });
  vi.spyOn(workflowApi, "list").mockResolvedValue({ missions: [mission] });
  vi.spyOn(workflowApi, "resumable").mockResolvedValue({ missions: [] });
  vi.spyOn(workflowApi, "deliverables").mockResolvedValue({ deliverables: [] });
});
afterEach(async () => {
  await cleanup();
  client.clear();
  vi.restoreAllMocks();
});

describe("one-turn Mission routing", () => {
  for (const mode of ["continue", "new"] as const) {
    const label = mode === "continue" ? "Continue mission" : "New mission";
    it(`${mode} resets on acceptance and leaves the next objective automatic`, async () => {
      let release: (value: Response) => void = () => {};
      const fetch = vi.spyOn(globalThis, "fetch").mockImplementationOnce(() => new Promise((resolve) => { release = resolve; })).mockResolvedValue(accepted());
      await host();
      await page.getByRole("button", { name: label, exact: true }).click();
      await page.getByRole("textbox", { name: "Message Nova" }).fill("Break that down by region");
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await expect.poll(() => fetch.mock.calls.length).toBe(1);
      await expect.element(page.getByRole("button", { name: label, exact: true })).toHaveAttribute("aria-pressed", "true");
      const first = JSON.parse(String(fetch.mock.calls[0][1]?.body));
      expect(first.continue_mission_id).toBe(mode === "continue" ? "revenue" : undefined);
      expect(first.new_mission).toBe(mode === "new");
      release(accepted());
      await expect.element(page.getByRole("button", { name: "Automatic", exact: true })).toHaveAttribute("aria-pressed", "true");
      await expect.element(page.getByRole("textbox", { name: "Message Nova" })).toBeEnabled();
      await page.getByRole("textbox", { name: "Message Nova" }).fill("Evaluate opening operations in Singapore");
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await expect.poll(() => fetch.mock.calls.length).toBe(2);
      const second = JSON.parse(String(fetch.mock.calls[1][1]?.body));
      expect(second.content).toBe("Evaluate opening operations in Singapore");
      expect(second.continue_mission_id).toBeUndefined();
      expect(second.new_mission).toBe(false);
    });
    it(`${mode} retains the draft and routing on rejection, then retries unchanged`, async () => {
      const fetch = vi.spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Mission access unavailable" }), { status: 409, headers: { "Content-Type": "application/json" } }))
        .mockResolvedValueOnce(accepted());
      await host();
      await page.getByRole("button", { name: label, exact: true }).click();
      await page.getByRole("textbox", { name: "Message Nova" }).fill("Inspect regional revenue");
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await expect.element(page.getByRole("textbox", { name: "Message Nova" })).toHaveValue("Inspect regional revenue");
      await expect.element(page.getByRole("alert")).toHaveTextContent("Mission access unavailable");
      await expect.element(page.getByRole("button", { name: label, exact: true })).toHaveAttribute("aria-pressed", "true");
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await expect.element(page.getByRole("button", { name: "Automatic", exact: true })).toHaveAttribute("aria-pressed", "true");
      expect(fetch.mock.calls[1][1]?.body).toBe(fetch.mock.calls[0][1]?.body);
    });
  }
  it("resumed Mission continuation applies only to the next accepted turn", async () => {
    vi.mocked(workflowApi.resumable).mockResolvedValue({ missions: [{ ...mission, resume_required: true }] });
    const resume = vi.spyOn(workflowApi, "resume").mockResolvedValue({ ...mission, revision: 4 });
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => accepted());
    await host();
    await page.getByRole("button", { name: "Resume mission", exact: true }).click();
    await expect.element(page.getByRole("button", { name: "Continue mission", exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(resume.mock.calls[0][0].revision).toBe(3);
    await page.getByRole("textbox", { name: "Message Nova" }).fill("Continue the investigation");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect.element(page.getByRole("button", { name: "Automatic", exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body)).continue_mission_id).toBe("revenue");
    await expect.element(page.getByRole("textbox", { name: "Message Nova" })).toBeEnabled();
    await page.getByRole("textbox", { name: "Message Nova" }).fill("Evaluate Singapore expansion");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => fetch.mock.calls.length).toBe(2);
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body)).continue_mission_id).toBeUndefined();
  });
});
