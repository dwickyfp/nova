import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
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
  await expect.element(page.getByRole("textbox", { name: "Message Nova" })).toBeEnabled();
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

describe("automatic chat routing", () => {
  it("retains a typed prompt until the selected conversation has loaded", async () => {
    let release: (value: Awaited<ReturnType<typeof agentsApi.getThread>>) => void = () => {};
    vi.mocked(agentsApi.getThread).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(accepted());
    await render(<QueryClientProvider client={client}><div className="flex h-dvh min-w-0 overflow-hidden">
      <StudioChat agent={agent} agents={[agent]} onSelectAgent={vi.fn()} currentRole="ANALYST" activeThreadId={mission.thread_id} onThreadChange={vi.fn()} />
    </div></QueryClientProvider>);
    const input = page.getByRole("textbox", { name: "Message Nova" });
    await input.fill("Create a city revenue report filtered to Jakarta");
    await expect.element(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
    await userEvent.keyboard("{Enter}");
    expect(fetch).not.toHaveBeenCalled();
    release({ thread: { thread_id: mission.thread_id } as never, messages: [] });
    await expect.element(page.getByRole("button", { name: "Send", exact: true })).toBeEnabled();
    await expect.element(input).toHaveValue("Create a city revenue report filtered to Jakarta");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => fetch.mock.calls.length).toBe(1);
    await expect.element(page.getByText("Create a city revenue report filtered to Jakarta", { exact: true })).toBeInTheDocument();
  });

  it("keeps the composer free of mission controls and routes messages automatically", async () => {
    vi.mocked(workflowApi.resumable).mockResolvedValue({ missions: [{ ...mission, resume_required: true }] });
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => accepted());
    await host();
    await expect.element(page.getByRole("region", { name: "Mission turn controls" })).not.toBeInTheDocument();
    for (const label of ["Automatic", "Continue mission", "New mission", "Resume mission"]) {
      await expect.element(page.getByRole("button", { name: label, exact: true })).not.toBeInTheDocument();
    }
    expect(workflowApi.resumable).not.toHaveBeenCalled();
    for (const content of ["Break that down by region", "Evaluate opening operations in Singapore"]) {
      await page.getByRole("textbox", { name: "Message Nova" }).fill(content);
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await expect.element(page.getByRole("button", { name: "Stop", exact: true })).not.toBeInTheDocument();
    }
    expect(fetch).toHaveBeenCalledTimes(2);
    for (const [, request] of fetch.mock.calls) {
      const body = JSON.parse(String(request?.body));
      expect(body.continue_mission_id).toBeUndefined();
      expect(body.new_mission).toBeUndefined();
    }
  });

  it("preserves the draft on rejection and retries the same request", async () => {
    const fetch = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Mission access unavailable" }), {
        status: 409, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(accepted());
    await host();
    await page.getByRole("textbox", { name: "Message Nova" }).fill("Inspect regional revenue");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect.element(page.getByRole("alert")).toHaveTextContent("Mission access unavailable");
    await expect.element(page.getByRole("textbox", { name: "Message Nova" })).toHaveValue("Inspect regional revenue");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => fetch.mock.calls.length).toBe(2);
    expect(fetch.mock.calls[1][1]?.body).toBe(fetch.mock.calls[0][1]?.body);
  });
});
