import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api-client";
import { WorkflowRail } from "./workflow-rail";
import { mergeMissionProjection, workflowApi, type Mission, type MissionDeliverable } from "./workflow-api";
import { MissionPanel } from "./mission-panel";
import "@/styles/index.css";

const mission: Mission = { mission_id: "m1", thread_id: "t1", objective: "Investigate the revenue decline", work_intent: "INVESTIGATE", status: "running", revision: 3,
  run_ids: ["r1"], stages: [{ kind: "evidence", label: "Inspect evidence", status: "completed", source_refs: ["r1"] }], evidence_refs: ["e1"], object_refs: [], cancel_requested: false, created_at: "2026-10-03T10:00:00Z", updated_at: "2026-10-03T10:00:00Z" };
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

function Host() {
  const [available, setAvailable] = useState(false);
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  return <div className="flex h-dvh min-w-0 overflow-hidden">
    <main className="flex min-h-0 min-w-0 flex-1 flex-col"><header className="shrink-0 p-3">Studio
      {available && <button ref={opener} className="ml-2 min-h-11 rounded border px-3" onClick={() => setOpen(true)}>Open workflow details</button>}
    </header><div className="min-h-0 flex-1 overflow-y-auto p-3">Conversation</div></main>
    <WorkflowRail threadId="t1" turns={[]} streaming={false} agents={[]} runs={[]} runLoading={false} runError={false} retryRuns={vi.fn()} onSelectChild={vi.fn()} onAvailable={setAvailable} mobileOpen={open}
      onMobileOpenChange={(value) => { setOpen(value); if (!value) requestAnimationFrame(() => opener.current?.focus()); }} />
  </div>;
}

afterEach(async () => { await cleanup(); vi.restoreAllMocks(); document.documentElement.classList.remove("dark"); await page.viewport(1280, 720); });

describe("Mission projections", () => {
  it("ignores old replay frames and frames from another thread", () => {
    const current = { missions: [mission] };
    expect(mergeMissionProjection(current, { ...mission, revision: 2 }, "t1")).toBe(current);
    expect(mergeMissionProjection(current, { ...mission, revision: 4, thread_id: "other" }, "t1")).toBe(current);
    expect(mergeMissionProjection(current, { ...mission, revision: 4, status: "completed" }, "t1")?.missions[0].status).toBe("completed");
  });
});

describe("Workflow rail", () => {
  for (const dark of [false, true]) it(`uses a sheet at 320px and supports keyboard tabs and Escape (dark: ${dark})`, async () => {
    await page.viewport(320, 720);
    document.documentElement.classList.toggle("dark", dark);
    vi.spyOn(workflowApi, "list").mockResolvedValue({ missions: [mission] });
    vi.spyOn(workflowApi, "deliverables").mockResolvedValue({ deliverables: [] });
    render(<QueryClientProvider client={client()}><Host /></QueryClientProvider>);
    await page.getByRole("button", { name: "Open workflow details" }).click();
    await expect.element(page.getByRole("dialog")).toBeVisible();
    await expect.element(page.getByText(mission.objective)).toBeVisible();
    page.getByRole("tab", { name: "Activity", exact: true }).element().focus();
    await userEvent.keyboard("{ArrowRight}");
    await expect.element(page.getByRole("tab", { name: "Evidence", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect.element(page.getByText("Evidence has not been assessed")).toBeVisible();
    await userEvent.keyboard("{ArrowRight}");
    await expect.element(page.getByLabelText("Find a business concept")).toBeVisible();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
    await userEvent.keyboard("{Escape}");
    await expect.element(page.getByRole("dialog")).not.toBeInTheDocument();
    await expect.element(page.getByRole("button", { name: "Open workflow details" })).toHaveFocus();
  });
  it("keeps desktop rail content inside the viewport", async () => {
    await page.viewport(1280, 720);
    vi.spyOn(workflowApi, "list").mockResolvedValue({ missions: [{ ...mission, objective: mission.objective.repeat(40) }] });
    vi.spyOn(workflowApi, "deliverables").mockResolvedValue({ deliverables: [] });
    render(<QueryClientProvider client={client()}><Host /></QueryClientProvider>);
    const rail = page.getByRole("complementary", { name: "Workflow details" });
    await expect.element(rail).toBeVisible();
    expect(rail.element().getBoundingClientRect().height).toBeLessThanOrEqual(720);
    expect(document.documentElement.scrollHeight).toBeLessThanOrEqual(720);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(1280);
  });
  it("hides unavailable workflows and permits retry after a service failure", async () => {
    const list = vi.spyOn(workflowApi, "list").mockRejectedValue(new ApiError(404, "Workflow disabled"));
    const screen = await render(<QueryClientProvider client={client()}><Host /></QueryClientProvider>);
    await expect.poll(() => list.mock.calls.length).toBe(1);
    await expect.element(page.getByRole("complementary", { name: "Workflow details" })).not.toBeInTheDocument();
    await screen.unmount();
    list.mockRejectedValueOnce(new ApiError(503, "Service unavailable")).mockResolvedValue({ missions: [] });
    render(<QueryClientProvider client={client()}><Host /></QueryClientProvider>);
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect.element(page.getByText("This conversation has no mission")).toBeVisible();
  });
});

describe("Mission operations", () => {
  it("keeps cancellation in its recorded state until the service confirms it", async () => {
    let complete: (value: Mission) => void = () => {};
    const cancel = vi.spyOn(workflowApi, "cancel").mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    vi.spyOn(workflowApi, "deliverables").mockResolvedValue({ deliverables: [] });
    const refresh = vi.fn();
    render(<QueryClientProvider client={client()}><MissionPanel mission={mission} onRefresh={refresh} /></QueryClientProvider>);
    await page.getByRole("button", { name: "Cancel mission", exact: true }).click();
    expect(cancel).toHaveBeenCalledWith(mission);
    await expect.element(page.getByText("running", { exact: true })).toBeVisible();
    expect(refresh).not.toHaveBeenCalled();
    complete({ ...mission, status: "cancelling", cancel_requested: true, revision: 4 });
    await expect.poll(() => refresh.mock.calls.length).toBe(1);
  });
  it("retries generation with the same operation and displays persisted references", async () => {
    const document: MissionDeliverable = { deliverable_id: "d1", mission_id: "m1", mission_revision: 3, kind: "decision_memo", title: "Decision memo", markdown: "# Recorded decision\nEvidence was inspected.", evidence_refs: ["e1"], object_refs: [], created_at: "2026-10-03T10:00:00Z" };
    let stored: MissionDeliverable[] = [];
    vi.spyOn(workflowApi, "deliverables").mockImplementation(async () => ({ deliverables: stored }));
    const deliver = vi.spyOn(workflowApi, "deliver").mockRejectedValueOnce(new Error("Temporary failure")).mockImplementation(async () => { stored = [document]; return document; });
    render(<QueryClientProvider client={client()}><MissionPanel mission={mission} onRefresh={vi.fn()} /></QueryClientProvider>);
    await page.getByRole("button", { name: "Generate decision memo", exact: true }).click();
    await expect.element(page.getByText("Temporary failure")).toBeVisible();
    await page.getByRole("button", { name: "Generate decision memo", exact: true }).click();
    await expect.element(page.getByRole("heading", { name: "Recorded decision" })).toBeVisible();
    expect(deliver.mock.calls[0][2]).toBe(deliver.mock.calls[1][2]);
    await page.getByText("Source references", { exact: true }).click();
    await expect.element(page.getByText("Evidence: e1", { exact: true })).toBeVisible();
  });
});
