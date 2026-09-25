import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentMemoryDialog } from "./agent-memory-dialog";
import "@/styles/index.css";

const mocks = vi.hoisted(() => ({
  memories: [] as Array<Record<string, string>>,
  list: vi.fn(),
  remove: vi.fn(),
  getAgent: vi.fn(),
  getModel: vi.fn(),
  listProposals: vi.fn(),
  createProposal: vi.fn(),
  previewProposal: vi.fn(),
  approveProposal: vi.fn(),
  rejectProposal: vi.fn(),
  proposals: [] as Array<Record<string, string>>,
}));

vi.mock("@/features/agents/api", () => ({
  agentsApi: {
    listMemories: mocks.list,
    deleteMemory: mocks.remove,
    get: mocks.getAgent,
    listRuleProposals: mocks.listProposals,
    createRuleProposal: mocks.createProposal,
    previewRuleProposal: mocks.previewProposal,
    approveRuleProposal: mocks.approveProposal,
    rejectRuleProposal: mocks.rejectProposal,
  },
}));
vi.mock("@/features/intelligence/semantic-views-api", () => ({
  semanticViewsApi: { get: mocks.getModel },
}));

beforeEach(() => {
  mocks.proposals = [];
  mocks.memories = [{
    memory_id: "m1",
    fact: "Omzet adalah invoice lunas dikurangi retur.",
    source_quote: "Omzet adalah invoice lunas dikurangi retur",
  }];
  mocks.list.mockImplementation(async () => ({
    memories: mocks.memories, count: mocks.memories.length, next_offset: null,
  }));
  mocks.remove.mockImplementation(async (_agentId: string, memoryId: string) => {
    mocks.memories = mocks.memories.filter((memory) => memory.memory_id !== memoryId);
  });
  mocks.getAgent.mockResolvedValue({ semantic_view_ids: ["view-1"] });
  mocks.getModel.mockResolvedValue({
    id: "view-1", name: "Sales", active_version: 1,
    versions: [{ version: 1, status: "ACTIVE", definition: {
      metrics: [{ name: "revenue", expression: "SUM(orders.amount)" }],
    } }],
  });
  mocks.listProposals.mockImplementation(async () => ({
    proposals: mocks.proposals, count: mocks.proposals.length,
  }));
  mocks.createProposal.mockImplementation(async () => {
    mocks.proposals = [{
      proposal_id: "proposal-1", memory_id: "m1", status: "pending",
      metric_name: "revenue", prior_expression: "SUM(orders.amount)",
      proposed_expression: "SUM(orders.amount) - SUM(orders.refunds)",
    }];
  });
  mocks.previewProposal.mockResolvedValue({
    proposal_id: "proposal-1", prior_value: "100", proposed_value: "80",
    prior_sql: "SELECT 100", proposed_sql: "SELECT 80", metric_name: "revenue",
  });
  mocks.approveProposal.mockImplementation(async () => {
    mocks.proposals = [{ ...mocks.proposals[0], status: "approved" }];
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  mocks.list.mockReset();
  mocks.remove.mockReset();
  for (const fn of [mocks.getAgent, mocks.getModel, mocks.listProposals,
    mocks.createProposal, mocks.previewProposal, mocks.approveProposal, mocks.rejectProposal]) {
    fn.mockReset();
  }
});

describe("AgentMemoryDialog", () => {
  it("shows a sourced memory and lets its owner delete it", async () => {
    await render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AgentMemoryDialog agentId="sales" />
      </QueryClientProvider>,
    );
    await page.getByRole("button", { name: "View agent memory" }).click();
    await expect.element(page.getByText("Omzet adalah invoice lunas dikurangi retur.")).toBeVisible();
    await expect.element(page.getByText(/From your message:/)).toBeVisible();
    await page.getByRole("button", { name: /Delete memory:/ }).click();
    await page.getByRole("button", { name: "Delete memory", exact: true }).click();
    expect(mocks.remove).toHaveBeenCalledWith("sales", "m1");
    await expect.element(page.getByText(/No memories yet/)).toBeVisible();
  });

  it("keeps the memory dialog inside a narrow viewport", async () => {
    await page.viewport(320, 640);
    try {
      await render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <AgentMemoryDialog agentId="sales" />
        </QueryClientProvider>,
      );
      await page.getByRole("button", { name: "View agent memory" }).click();
      const dialog = page.getByRole("dialog").element();
      expect(dialog.getBoundingClientRect().right).toBeLessThanOrEqual(320);
      expect(dialog.scrollWidth).toBeLessThanOrEqual(dialog.clientWidth);
    } finally {
      await page.viewport(1280, 720);
    }
  });

  it("loads older memories on request", async () => {
    mocks.list.mockImplementation(async (_agentId: string, offset: number) =>
      offset === 0
        ? { memories: mocks.memories, count: 1, next_offset: 100 }
        : { memories: [{ memory_id: "m2", fact: "Retur mengurangi omzet.",
                        source_quote: "Retur mengurangi omzet" }], count: 1, next_offset: null },
    );
    await render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AgentMemoryDialog agentId="sales" />
      </QueryClientProvider>,
    );
    await page.getByRole("button", { name: "View agent memory" }).click();
    await page.getByRole("button", { name: "Load more memories" }).click();
    await expect.element(page.getByText("Retur mengurangi omzet.")).toBeVisible();
    expect(mocks.list).toHaveBeenCalledWith("sales", 100);
  });

  it("keeps loaded memories visible when a later page fails", async () => {
    mocks.list.mockImplementation(async (_agentId: string, offset: number) => {
      if (offset > 0) throw new Error("offline");
      return { memories: mocks.memories, count: 1, next_offset: 100 };
    });
    await render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AgentMemoryDialog agentId="sales" />
      </QueryClientProvider>,
    );
    await page.getByRole("button", { name: "View agent memory" }).click();
    await page.getByRole("button", { name: "Load more memories" }).click();
    await expect.element(page.getByText("Omzet adalah invoice lunas dikurangi retur.")).toBeVisible();
    await expect.element(page.getByText(/More memories could not be loaded/)).toBeVisible();
  });

  it("requires a preview before approving a remembered rule", async () => {
    await render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AgentMemoryDialog agentId="sales" />
      </QueryClientProvider>,
    );
    await page.getByRole("button", { name: "View agent memory" }).click();
    await page.getByRole("button", { name: "Propose as business rule" }).click();
    await expect.element(page.getByLabelText("Proposed metric expression")).toBeVisible();
    await page.getByLabelText("Proposed metric expression").fill(
      "SUM(orders.amount) - SUM(orders.refunds)",
    );
    await page.getByRole("button", { name: "Save proposal" }).click();
    await expect.element(page.getByRole("button", { name: "Approve rule" })).toBeDisabled();
    await page.getByRole("button", { name: "Preview impact" }).click();
    await expect.element(page.getByText("Proposed: 80")).toBeVisible();
    await page.getByRole("button", { name: "Approve rule" }).click();
    expect(mocks.approveProposal).toHaveBeenCalledWith("view-1", "proposal-1");
  });

  it("keeps the proposal review inside a narrow viewport", async () => {
    await page.viewport(320, 640);
    try {
      await render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <AgentMemoryDialog agentId="sales" />
        </QueryClientProvider>,
      );
      await page.getByRole("button", { name: "View agent memory" }).click();
      await page.getByRole("button", { name: "Propose as business rule" }).click();
      await expect.element(page.getByLabelText("Proposed metric expression")).toBeVisible();
      const dialog = page.getByRole("dialog").element();
      expect(dialog.getBoundingClientRect().right).toBeLessThanOrEqual(320);
      expect(dialog.scrollWidth).toBeLessThanOrEqual(dialog.clientWidth);
    } finally {
      await page.viewport(1280, 720);
    }
  });
});
