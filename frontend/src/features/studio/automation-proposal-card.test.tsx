import { afterEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { automationsApi, type Automation } from "@/features/agents/studio-intelligence-api";
import type { AutomationProposal } from "@/features/assistant/types";
import { AutomationProposalCard } from "./automation-proposal-card";
import "@/styles/index.css";

const PROPOSAL: AutomationProposal = {
  title: "Weekly expense",
  prompt: "Berapa total expense minggu lalu?",
  schedule_kind: "cron",
  schedule_expr: "0 8 * * 1",
  timezone: "Asia/Jakarta",
  condition: { metric: "total_expense", operator: ">", value: 5000000000 },
};

function show(existing: Automation[] = []) {
  vi.spyOn(automationsApi, "list").mockResolvedValue({ automations: existing, count: existing.length });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AutomationProposalCard proposal={PROPOSAL} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("AutomationProposalCard", () => {
  it("says what would run in plain words and schedules nothing by itself", async () => {
    const create = vi.spyOn(automationsApi, "create");
    await show();
    const card = page.getByRole("region", { name: "Proposed schedule" });
    await expect.element(card).toHaveTextContent("every Monday at 08:00 (Asia/Jakarta)");
    await expect.element(card).toHaveTextContent("only when total expense is >");
    await expect.element(card).toHaveTextContent("Nothing is scheduled yet.");
    expect(create).not.toHaveBeenCalled();
  });

  it("creates the Smart automation only when the user confirms", async () => {
    const create = vi.spyOn(automationsApi, "create").mockResolvedValue({} as Automation);
    await show();
    await page.getByRole("button", { name: "Schedule" }).click();
    expect(create).toHaveBeenCalledWith("__smart__", {
      title: "Weekly expense",
      prompt: "Berapa total expense minggu lalu?",
      schedule_kind: "cron",
      schedule_expr: "0 8 * * 1",
      timezone: "Asia/Jakarta",
      condition: PROPOSAL.condition,
    });
  });

  it("shows a schedule that already exists as scheduled, with no second button", async () => {
    await show([{
      automation_id: "au1", agent_id: "__smart__", title: PROPOSAL.title, prompt: PROPOSAL.prompt,
      schedule_kind: "cron", schedule_expr: PROPOSAL.schedule_expr, timezone: PROPOSAL.timezone,
      condition: null, delivery: null, enabled: true, next_run_at: null, last_run_at: null,
      last_status: null, last_thread_id: null,
    }]);
    const card = page.getByRole("region", { name: "Proposed schedule" });
    await expect.element(card).toHaveTextContent("Scheduled.");
    await expect.element(page.getByRole("button", { name: "Schedule" })).not.toBeInTheDocument();
  });

  it("goes away when the user declines", async () => {
    await show();
    await page.getByRole("button", { name: "Not now" }).click();
    await expect.element(page.getByRole("region", { name: "Proposed schedule" })).not.toBeInTheDocument();
  });
});
