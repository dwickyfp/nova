import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/index.css";
import { AgentAccessTab } from "./access-tab";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  verify: vi.fn(),
  newChatAndSend: vi.fn(),
}));

vi.mock("@/features/assistant/assistant-provider", () => ({
  useAssistant: () => ({ newChatAndSend: mocks.newChatAndSend }),
}));

vi.mock("@/features/agents/api", () => ({
  agentAccessApi: {
    list: mocks.list,
    verify: mocks.verify,
    add: vi.fn(),
    remove: vi.fn(),
  },
  rolesApi: { list: vi.fn(async () => ({ roles: [] })) },
}));

function renderAccessTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AgentAccessTab agentId="sales" agentName="Sales Agent" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mocks.list.mockReset().mockResolvedValue({
    roles: [
      {
        role_name: "ACCOUNTADMIN",
        grant_type: "USAGE",
        verified: false,
        verified_at: null,
      },
    ],
    count: 1,
  });
  mocks.verify.mockReset();
  mocks.newChatAndSend.mockReset();
});

afterEach(async () => {
  await page.viewport(1440, 900);
});

describe("AgentAccessTab verification", () => {
  it("shows missing permissions within the role instead of a bottom drawer", async () => {
    mocks.verify.mockResolvedValue({
      role_name: "ACCOUNTADMIN",
      all_granted: false,
      checked_at: "2026-09-23T00:00:00Z",
      items: [
        {
          kind: "table",
          name: "NOVA_SALES.fact_sales",
          granted: false,
          detail: "No Ranger permission.",
        },
      ],
    });
    const screen = await renderAccessTab();

    await screen.getByRole("button", { name: "Verify" }).click();

    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("1 access gap found");
    await expect
      .element(screen.getByText("NOVA_SALES.fact_sales"))
      .toBeVisible();
    await expect.element(screen.getByText("SELECT")).toBeVisible();
    await expect
      .element(screen.getByRole("link", { name: "Open Access Control" }))
      .toHaveAttribute("href", "/access-control");
    expect(document.querySelector(".fixed.inset-x-0.bottom-0")).toBeNull();

    const askNove = screen.getByRole("button", {
      name: "Ask Nove to grant missing access",
    });
    expect(askNove.element().nextElementSibling).toBe(
      screen
        .getByRole("button", { name: "Dismiss verification result" })
        .element(),
    );
    await askNove.click();
    expect(mocks.newChatAndSend).toHaveBeenCalledOnce();
    const prompt = mocks.newChatAndSend.mock.calls[0][0] as string;
    expect(prompt).toContain('role "ACCOUNTADMIN"');
    expect(prompt).toContain('agent "Sales Agent"');
    expect(prompt).toContain('SELECT on "NOVA_SALES.fact_sales"');
    expect(prompt).toContain(
      "Keep this exact role; do not create or suggest a replacement role",
    );
    expect(prompt).toContain("adding a Ranger access policy for it is allowed");
    expect(prompt).toContain(
      "StarRocks native grants do not satisfy Verify Access",
    );
    expect(prompt).toContain("ask for my approval before applying them");
  });

  it("keeps request failures next to the role", async () => {
    mocks.verify.mockRejectedValue(
      new Error("Verification service unavailable"),
    );
    const screen = await renderAccessTab();

    await screen.getByRole("button", { name: "Verify" }).click();

    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("Verification could not finish");
    await expect
      .element(screen.getByText("Verification service unavailable"))
      .toBeVisible();
  });

  it("keeps the grant action reachable on a phone-sized screen", async () => {
    await page.viewport(375, 800);
    mocks.verify.mockResolvedValue({
      role_name: "ACCOUNTADMIN",
      all_granted: false,
      checked_at: "2026-09-23T00:00:00Z",
      items: [
        {
          kind: "table",
          name: "NOVA_SALES.vw_sales_rep_monthly_performance",
          granted: false,
          detail: "No Ranger permission.",
        },
      ],
    });
    const screen = await renderAccessTab();
    await screen.getByRole("button", { name: "Verify" }).click();

    const button = screen.getByRole("button", {
      name: "Ask Nove to grant missing access",
    });
    await expect.element(button).toBeVisible();
    expect(
      button.element().getBoundingClientRect().height,
    ).toBeGreaterThanOrEqual(44);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
      window.innerWidth,
    );
  });
});
