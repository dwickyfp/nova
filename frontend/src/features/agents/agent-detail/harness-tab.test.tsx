import { afterEach, describe, expect, it, vi } from "vitest";
import { userEvent } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentHarnessTab } from "./harness-tab";
import type { HarnessExperiment, HarnessSurface } from "../harness-api";
import "@/styles/index.css";

const surface: HarnessSurface = {
  components: [
    { key: "instructions", load: "always", count: 2, chars: 120 },
    { key: "standing_rules", load: "always", count: 1, chars: 34 },
    { key: "tool_guidance", load: "always", count: 0, chars: 0 },
    { key: "skills", load: "on_demand", count: 0, default: [], discoverable: [] },
    { key: "procedures", load: "on_demand", count: 0, names: [] },
    {
      key: "guards",
      load: "in_loop",
      count: 2,
      platform: [
        {
          rule_id: "untrusted-instructions-in-data",
          stage: "after_tool",
          description: "Labels result values that try to instruct the agent.",
        },
      ],
      agent: [],
    },
    { key: "memory", load: "retrieved", count: null },
    {
      key: "budget",
      load: "every_turn",
      count: null,
      profile: "analyst",
      max_iterations: 16,
      time_budget_seconds: 180,
      max_calls_per_tool: 6,
      spend_tokens: 320000,
    },
  ],
  always_on: { harness_chars: 34, harness_cap: 4000, contract_chars: 200, contract_cap: 4800 },
  tools: ["semantic_query", "data_to_chart"],
  policy: { standing_rules: ["State the period of every number."], tool_guidance: {}, guards: [] },
  issues: [
    { severity: "warning", component: "tool_guidance", message: "guidance for 'ml_execute'" },
  ],
};

const experiment: HarnessExperiment = {
  id: "exp-1",
  status: "passed",
  version_id: "v1",
  cases: [{ id: "c1" }, { id: "c2" }],
  stages: {
    baseline: { total_tokens: 1000, stage_duration_ms: 4000 },
    propose_inv: { total_tokens: 300, stage_duration_ms: 1000 },
  },
  total_tokens: 1300,
  recommended_version_id: "v2",
  selected_label: "inv",
  rationale: "Adopted inv: it improved measured assertions with no per-assertion regression",
  edit_rows: [
    {
      candidate: "inv",
      components: ["guards"],
      notes: "case 1 queried per salesman",
      improved: ["c1:guard_compliance"],
      regressed: [],
      passes: 4,
      assertions: 4,
      verdict: "helped",
      fell_back: false,
      adopted: true,
    },
    {
      candidate: "dom",
      components: ["standing_rules", "tool_guidance"],
      notes: "",
      improved: ["c2:task_completeness"],
      regressed: ["c1:tool_selection"],
      passes: 3,
      assertions: 4,
      verdict: "confounded",
      fell_back: false,
      adopted: false,
    },
  ],
  started_at: "2026-11-07T01:00:00Z",
};

const json = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );

function mockApi(overrides: { experiments?: HarnessExperiment[] } = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url.endsWith("/harness")) return json(surface);
    if (url.endsWith("/quality/harness-experiments") && init?.method === "POST")
      return json({ ...experiment, status: "running", active: true, id: "exp-2" });
    if (url.endsWith("/quality/harness-experiments"))
      return json({ experiments: overrides.experiments ?? [experiment] });
    if (url.endsWith("/quality/harness-lessons")) return json({ lessons: [] });
    if (url.includes("/quality/cases"))
      return json({ items: [{ id: "c1", name: "Revenue by city", revision: 1 }] });
    if (url.includes("/versions"))
      return json({
        versions: [{ version_id: "v1", label: "Active configuration" }],
        active_version_id: "v1",
      });
    if (url.match(/\/agents\/sales$/))
      return json({
        agent_id: "sales",
        name: "SerbaData",
        harness_policy: surface.policy,
        default_tools: ["semantic_query"],
        updated_at: "2026-11-07T00:00:00Z",
      });
    return json({});
  });
}

const setup = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      <AgentHarnessTab agentId="sales" />
    </QueryClientProvider>,
  );

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
});

describe("AgentHarnessTab", () => {
  it("shows every component, the always-on budget and validation problems", async () => {
    mockApi();
    const screen = await setup();
    await expect.element(screen.getByRole("table", { name: /harness components/i })).toBeVisible();
    await expect.element(screen.getByRole("rowheader", { name: "Safety rules" })).toBeVisible();
    await expect.element(screen.getByText(/320,000 tokens per turn/)).toBeVisible();
    await expect.element(screen.getByRole("meter", { name: /read on every question/i })).toHaveAttribute(
      "aria-valuenow",
      "34",
    );
    await expect.element(screen.getByText(/guidance for 'ml_execute'/)).toBeVisible();
    await expect.element(screen.getByText("untrusted-instructions-in-data")).toBeVisible();
  });

  it("shows each candidate's verdict and the adopted harness", async () => {
    mockApi();
    const screen = await setup();
    await expect.element(screen.getByText("Improvement found")).toBeVisible();
    await expect.element(screen.getByText("How it works (adopted)")).toBeVisible();
    await expect.element(screen.getByText("confounded")).toBeVisible();
    await expect.element(screen.getByText("+1 / −1")).toBeVisible();
    await expect.element(screen.getByText(/Harness experiment inv/)).toBeVisible();
  });

  it("starts an experiment only with a version, cases and a valid budget", async () => {
    const fetch = mockApi({ experiments: [] });
    const screen = await setup();
    const startButton = screen.getByRole("button", { name: "Start experiment" });
    await expect.element(startButton).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Revenue by city"));
    await userEvent.clear(screen.getByLabelText("Token budget"));
    await userEvent.type(screen.getByLabelText("Token budget"), "500");
    await expect.element(startButton).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Token budget"));
    await userEvent.type(screen.getByLabelText("Token budget"), "60000");
    await expect.element(startButton).toBeEnabled();
    await userEvent.click(startButton);
    await vi.waitFor(() => {
      const call = fetch.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/quality/harness-experiments") && init?.method === "POST",
      );
      expect(call).toBeDefined();
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body).toMatchObject({ version_id: "v1", case_ids: ["c1"], max_tokens: 60000 });
      expect(body.operation_id).toMatch(/^ui-/);
    });
  });

  it("explains what to fix before a guard can be saved", async () => {
    mockApi();
    const screen = await setup();
    await userEvent.click(screen.getByRole("button", { name: "Add guard" }));
    const save = screen.getByRole("button", { name: "Save" });
    await expect.element(save).toBeDisabled();
    const problems = screen.getByRole("list", { name: "Fix before saving" });
    await expect.element(problems).toHaveTextContent(/add at least one phrase to match/);
    await expect.element(problems).toHaveTextContent(/message of at least 8 characters/);
    await userEvent.type(
      screen.getByLabelText(/Matches any of these phrases/),
      "salesman",
    );
    await userEvent.type(
      screen.getByLabelText("Message the agent reads"),
      "Use the city dimension instead.",
    );
    await expect.element(save).toBeEnabled();
  });

  it("says what to do when the baseline is pinned to changed dependencies", async () => {
    mockApi({
      experiments: [{
        ...experiment,
        status: "unavailable",
        edit_rows: [],
        error: "Experiment incomplete; baseline retained",
        diagnosis: "DEPENDENCY_DRIFT",
      }],
    });
    const screen = await setup();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent(/Save a new draft version and run the experiment on it/);
  });
});
