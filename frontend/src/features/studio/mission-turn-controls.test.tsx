import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MissionTurnControls, type MissionTurnChoice } from "./mission-turn-controls";
import { workflowApi, type Mission } from "./workflow-api";
import "@/styles/index.css";

const mission: Mission = {
  mission_id: "m1", thread_id: "t1", objective: "Revenue investigation", work_intent: "INVESTIGATE",
  status: "blocked", revision: 3, run_ids: [], stages: [], evidence_refs: ["q1"], object_refs: [],
  cancel_requested: false, created_at: "2026-10-04T00:00:00Z", updated_at: "2026-10-04T00:00:00Z",
};
function Host() {
  const [choice, setChoice] = useState<MissionTurnChoice>({ mode: "automatic" });
  return <MissionTurnControls threadId="t1" value={choice} onChange={setChoice} disabled={false} />;
}
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
afterEach(async () => { await cleanup(); vi.restoreAllMocks(); document.documentElement.classList.remove("dark"); await page.viewport(1280, 720); });

describe("Mission turn controls", () => {
  for (const dark of [false, true]) it(`selects explicit controls by keyboard without overflow (dark: ${dark})`, async () => {
    await page.viewport(320, 720);
    document.documentElement.classList.toggle("dark", dark);
    vi.spyOn(workflowApi, "list").mockResolvedValue({ missions: [mission] });
    vi.spyOn(workflowApi, "resumable").mockResolvedValue({ missions: [] });
    render(<QueryClientProvider client={client()}><Host /></QueryClientProvider>);
    const button = page.getByRole("button", { name: "Continue mission", exact: true });
    await expect.element(button).toBeEnabled();
    button.element().focus();
    await userEvent.keyboard("{Enter}");
    await expect.element(button).toHaveAttribute("aria-pressed", "true");
    await expect.element(page.getByLabelText("Mission to continue")).toHaveValue("m1");
    await page.getByRole("button", { name: "New mission", exact: true }).click();
    await expect.element(page.getByRole("button", { name: "New mission", exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
  });
  it("resumes with the same operation on retry and selects the new binding", async () => {
    vi.spyOn(workflowApi, "list").mockResolvedValue({ missions: [] });
    const summary = { ...mission, resume_required: true };
    vi.spyOn(workflowApi, "resumable").mockResolvedValue({ missions: [summary] });
    const resume = vi.spyOn(workflowApi, "resume").mockRejectedValueOnce(new Error("Access temporarily unavailable")).mockResolvedValue({ ...mission, revision: 4 });
    render(<QueryClientProvider client={client()}><Host /></QueryClientProvider>);
    await page.getByRole("button", { name: "Resume mission", exact: true }).click();
    await expect.element(page.getByRole("alert")).toHaveTextContent("Access temporarily unavailable");
    await page.getByRole("button", { name: "Resume mission", exact: true }).click();
    await expect.element(page.getByRole("status")).toHaveTextContent("Mission resumed with current access.");
    expect(resume.mock.calls[0][1]).toBe(resume.mock.calls[1][1]);
    expect(resume.mock.calls[0][0].revision).toBe(3);
    await expect.element(page.getByRole("button", { name: "Continue mission", exact: true })).toHaveAttribute("aria-pressed", "true");
  });
  it("keeps failed authorization visible and does not select historical work", async () => {
    vi.spyOn(workflowApi, "list").mockResolvedValue({ missions: [] });
    vi.spyOn(workflowApi, "resumable").mockResolvedValue({ missions: [{ ...mission, resume_required: true }] });
    vi.spyOn(workflowApi, "resume").mockRejectedValue(new Error("Semantic View access revoked"));
    render(<QueryClientProvider client={client()}><Host /></QueryClientProvider>);
    await page.getByRole("button", { name: "Resume mission", exact: true }).click();
    await expect.element(page.getByRole("alert")).toHaveTextContent("Semantic View access revoked");
    await expect.element(page.getByRole("button", { name: "Continue mission", exact: true })).toBeDisabled();
    await expect.element(page.getByRole("button", { name: "Automatic", exact: true })).toHaveAttribute("aria-pressed", "true");
  });
});
