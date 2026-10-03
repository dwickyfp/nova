import { afterEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentQualityTab, QualitySuggestions } from "./quality-tab";
import { QualityRunComparison, QualityRunResults } from "./quality-results";
import { AgentImproveTab } from "./improve-tab";
import { QualityProposalDraft } from "./quality-proposal-draft";
import { useAuthStore } from "@/stores/auth-store";
import type { QualityCase, QualityRun } from "../quality-api";
import "@/styles/index.css";

const item: QualityCase = {
  id: "case-1",
  revision: 1,
  name: "Revenue evidence",
  prompt: "What is net revenue?",
  critical: true,
  mandatory: true,
  assertions: [
    {
      scorer: "numeric_consistency",
      required: true,
      expected: { accepted: true, unsupported_count: 0 },
    },
  ],
};
const run: QualityRun = {
  id: "run-1",
  revision: 2,
  agent_id: "sales",
  version_id: "draft",
  manifest_id: "manifest-1",
  status: "failed",
  promotion_eligible: false,
  created_at: "2026-10-03T01:00:00Z",
  results: [
    {
      case_id: item.id,
      case_revision: 1,
      status: "failed",
      trace_id: "trace-1",
      duration_ms: 12,
      scores: [
        {
          scorer: "numeric_consistency",
          required: true,
          status: "fail",
          detail: "Unsupported numeric claim",
          failure_taxonomy: "UNSUPPORTED_CONCLUSION",
        },
      ],
    },
  ],
};
const json = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
const setup = (node: React.ReactNode) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      {node}
    </QueryClientProvider>,
  );
function textContrast(element: Element) {
  const canvas = document.createElement("canvas");
  canvas.width = 2;
  canvas.height = 1;
  const context = canvas.getContext("2d")!;
  const ancestors: Element[] = [];
  for (
    let ancestor: Element | null = element;
    ancestor;
    ancestor = ancestor.parentElement
  )
    ancestors.unshift(ancestor);
  context.fillStyle = "white";
  context.fillRect(0, 0, 2, 1);
  for (const ancestor of ancestors) {
    context.fillStyle = getComputedStyle(ancestor).backgroundColor;
    context.fillRect(0, 0, 2, 1);
  }
  context.fillStyle = getComputedStyle(element).color;
  context.fillRect(1, 0, 1, 1);
  const pixels = context.getImageData(0, 0, 2, 1).data;
  const luminance = (offset: number) => {
    const channels = [...pixels.slice(offset, offset + 3)].map((value) => {
      const channel = value / 255;
      return channel <= 0.04045
        ? channel / 12.92
        : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const foreground = luminance(4),
    background = luminance(0);
  return (
    (Math.max(foreground, background) + 0.05) /
    (Math.min(foreground, background) + 0.05)
  );
}
function mock(evaluation: QualityRun = run) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url.endsWith("/quality/cases") && init?.method === "POST")
      return json({
        ...item,
        ...JSON.parse(init.body as string),
        id: "case-2",
      });
    if (url.endsWith("/quality/cases")) return json({ items: [item] });
    if (url.includes("/versions?"))
      return json({
        versions: [{ version_id: "draft", label: "Draft 1" }],
        active_version_id: "draft",
      });
    if (url.endsWith("/manifest"))
      return json(
        init?.method === "POST"
          ? { id: "manifest-1" }
          : { manifest: null, status: "unevaluated" },
      );
    if (url.endsWith("/quality/runs"))
      return json(
        init?.method === "POST" ? evaluation : { items: [evaluation] },
      );
    if (url.endsWith("/quality/runs/run-1")) return json(evaluation);
    if (url.endsWith("/quality/monitoring"))
      return json({
        enabled: false,
        sample_rate: 0.1,
        max_traces: 20,
        revision: 3,
      });
    if (url.endsWith("/quality/proposals")) return json({ items: [] });
    if (url.endsWith("/analyze"))
      return json({ id: "proposal-1", status: "proposed" });
    if (url.endsWith("/readiness")) return json({ checks: [] });
    if (url.endsWith("/improvement-suggestions"))
      return json({ feedback: [], materialized_views: [] });
    if (url.endsWith("/verified-candidates")) return json({ candidates: [] });
    return json({});
  });
}
afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
  await page.viewport(1280, 800);
});

describe("Agent Quality", () => {
  it("sends selected behavioral gates and bounded performance limits with the evaluation", async () => {
    await page.viewport(1280, 800);
    const fetch = mock();
    const screen = await setup(<AgentQualityTab agentId="sales" />);
    await screen.getByText("Promotion gates", { exact: true }).click();
    await screen.getByLabelText("Other case gates").click();
    await screen
      .getByRole("option", { name: "Require all other cases" })
      .click();
    await expect
      .element(screen.getByLabelText("Other case gates"))
      .toHaveFocus();
    await screen.getByLabelText("tool selection", { exact: true }).click();
    await expect
      .element(screen.getByLabelText("tool selection", { exact: true }))
      .toHaveFocus();
    await expect
      .element(screen.getByLabelText("tool selection", { exact: true }))
      .toBeChecked();
    await userEvent.keyboard(" ");
    await expect
      .element(screen.getByLabelText("tool selection", { exact: true }))
      .not.toBeChecked();
    await expect
      .element(screen.getByLabelText("tool selection", { exact: true }))
      .toHaveFocus();
    await userEvent.keyboard(" ");
    await expect
      .element(screen.getByLabelText("tool selection", { exact: true }))
      .toBeChecked();
    await screen.getByLabelText("Performance gate mode").click();
    await screen
      .getByRole("option", { name: "Required for promotion" })
      .click();
    await expect
      .element(screen.getByLabelText("Performance gate mode"))
      .toHaveFocus();
    await screen.getByLabelText("Maximum latency (ms)").fill("1250.5");
    await screen.getByLabelText("Maximum tool calls").fill("0");
    await screen.getByLabelText("Maximum provider calls").fill("4");
    await screen.getByLabelText("Maximum total tokens").fill("1200");
    await screen.getByLabelText("Maximum context tokens").fill("800");
    await screen.getByLabelText("Maximum participants").fill("2");
    await screen.getByLabelText("Maximum metadata reads").fill("3");
    await page.screenshot();
    await screen.getByRole("button", { name: "Evaluate release" }).click();
    await vi.waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/quality/runs") && init?.method === "POST",
        ),
      ).toBe(true),
    );
    const evaluation = fetch.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/quality/runs") && init?.method === "POST",
    );
    expect(JSON.parse(evaluation![1]!.body as string)).toEqual({
      version_id: "draft",
      gates: {
        other_cases: "all",
        required_scorers: ["tool_selection"],
        performance: "required",
        max_latency_ms: 1250.5,
        count_budgets: {
          tool_calls: 0,
          provider_calls: 4,
          total_tokens: 1200,
          context_tokens: 800,
          participants: 2,
          metadata_reads: 3,
        },
      },
    });
    await expect
      .element(screen.getByText("Unsupported numeric claim"))
      .toBeVisible();
  });
  it("keeps performance report only and omits limits that were cleared", async () => {
    const fetch = mock();
    const screen = await setup(<AgentQualityTab agentId="sales" />);
    await screen.getByText("Promotion gates", { exact: true }).click();
    await screen.getByLabelText("Other case gates").click();
    await screen
      .getByRole("option", { name: "Require other mandatory cases" })
      .click();
    await screen.getByLabelText("Maximum latency (ms)").fill("10");
    await screen.getByLabelText("Maximum latency (ms)").fill("");
    await screen.getByLabelText("Maximum tool calls").fill("1");
    await screen.getByLabelText("Maximum tool calls").fill("");
    await screen.getByLabelText("Maximum metadata reads").fill("5");
    await screen.getByLabelText("Maximum metadata reads").fill("");
    await screen.getByLabelText("Maximum total tokens").fill("0");
    await screen.getByRole("button", { name: "Evaluate release" }).click();
    await vi.waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/quality/runs") && init?.method === "POST",
        ),
      ).toBe(true),
    );
    const evaluation = fetch.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/quality/runs") && init?.method === "POST",
    );
    expect(JSON.parse(evaluation![1]!.body as string).gates).toEqual({
      other_cases: "mandatory",
      required_scorers: [],
      performance: "report_only",
      max_latency_ms: null,
      count_budgets: { total_tokens: 0 },
    });
    await expect
      .element(screen.getByText("Unsupported numeric claim"))
      .toBeVisible();
  });
  it("blocks invalid limits before pinning a manifest or starting an evaluation", async () => {
    const fetch = mock();
    const screen = await setup(<AgentQualityTab agentId="sales" />);
    await screen.getByText("Promotion gates", { exact: true }).click();
    for (const value of ["-1", "1.5", "9007199254740992"]) {
      await screen.getByLabelText("Maximum tool calls").fill(value);
      await expect
        .element(screen.getByRole("alert"))
        .toHaveTextContent("Maximum tool calls must be a whole number");
      await expect
        .element(screen.getByRole("button", { name: "Evaluate release" }))
        .toBeDisabled();
    }
    await screen.getByLabelText("Maximum tool calls").fill("9007199254740991");
    for (const theme of ["light", "dark"]) {
      await page.viewport(320, 900);
      document.documentElement.classList.toggle("dark", theme === "dark");
      await screen.getByLabelText("Maximum metadata reads").fill("1.5");
      await expect
        .element(screen.getByRole("alert"))
        .toHaveTextContent("Maximum metadata reads must be a whole number");
      await expect
        .element(screen.getByRole("button", { name: "Evaluate release" }))
        .toBeDisabled();
      screen.getByRole("alert").element().scrollIntoView({ block: "nearest" });
      await expect.element(screen.getByRole("alert")).toBeVisible();
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
      await page.screenshot();
    }
    await screen.getByLabelText("Maximum metadata reads").fill("0");
    for (const value of ["-0.5", "1e309"]) {
      await screen.getByLabelText("Maximum latency (ms)").fill(value);
      await expect
        .element(screen.getByRole("alert"))
        .toHaveTextContent("Maximum latency (ms) must be a finite number");
      await expect
        .element(screen.getByRole("button", { name: "Evaluate release" }))
        .toBeDisabled();
    }
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(
      false,
    );
    await screen.getByLabelText("Maximum latency (ms)").fill("0");
    await expect
      .element(screen.getByRole("button", { name: "Evaluate release" }))
      .toBeEnabled();
  });
  it("shows critical, scorer and performance gate assessments separately without deriving passes from case status", async () => {
    const screen = await setup(
      <QualityRunResults
        run={{
          ...run,
          gate_results: {
            mandatory_critical: {
              status: "passed",
              required: true,
              case_ids: [item.id],
            },
            other_quality: {
              status: "failed",
              required: true,
              case_ids: [],
              required_scorers: ["numeric_consistency"],
              mode: "report_only",
            },
            performance: {
              status: "unavailable",
              required: true,
              mode: "required",
              max_latency_ms: 0,
              count_budgets: { total_tokens: 0 },
            },
          },
          results: [
            {
              ...run.results[0],
              budget_scores: [
                {
                  scorer: "efficiency",
                  status: "unavailable",
                  required: true,
                  detail: "Token measurement was not recorded",
                },
              ],
            },
          ],
        }}
      />,
    );
    const gates = screen.getByRole("region", {
      name: "Promotion gate results",
    });
    await expect
      .element(gates.getByText("Mandatory critical cases"))
      .toBeVisible();
    await expect
      .element(gates.getByText("Passed", { exact: true }))
      .toBeVisible();
    await expect
      .element(gates.getByText("Additional case and scorer gates"))
      .toBeVisible();
    await expect
      .element(gates.getByText("Failed", { exact: true }))
      .toBeVisible();
    await expect
      .element(
        gates.getByText("Required in every case: numeric consistency.", {
          exact: false,
        }),
      )
      .toBeVisible();
    await expect
      .element(gates.getByText("Performance gates", { exact: true }))
      .toBeVisible();
    await expect
      .element(gates.getByText("Unavailable", { exact: true }))
      .toBeVisible();
    await expect
      .element(
        gates.getByText("Maximum latency: 0 ms · Total tokens: at most 0"),
      )
      .toBeVisible();
    await expect
      .element(screen.getByText("Token measurement was not recorded"))
      .toBeVisible();
    await expect
      .element(screen.getByRole("status"))
      .toHaveTextContent("Promotion gates have not passed.");
    for (const theme of ["", "dark"]) {
      document.documentElement.classList.toggle("dark", theme === "dark");
      for (const width of [320, 768, 1280]) {
        await page.viewport(width, 800);
        expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
      }
      for (const status of document.querySelectorAll(
        '[data-slot="status-badge"]',
      ))
        expect(
          textContrast(status),
          `${theme || "light"}: ${status.textContent}`,
        ).toBeGreaterThanOrEqual(4.5);
    }
  });
  it("reports failed performance without blocking passed required gates", async () => {
    const screen = await setup(
      <QualityRunResults
        run={{
          ...run,
          status: "passed",
          promotion_eligible: true,
          gate_results: {
            mandatory_critical: {
              status: "passed",
              required: true,
              case_ids: [item.id],
            },
            other_quality: {
              status: "not_configured",
              required: false,
              case_ids: [],
              required_scorers: [],
              mode: "report_only",
            },
            performance: {
              status: "failed",
              required: false,
              mode: "report_only",
              max_latency_ms: 10,
              count_budgets: {},
            },
          },
          results: [
            {
              ...run.results[0],
              budget_scores: [
                {
                  scorer: "latency",
                  status: "fail",
                  required: true,
                  detail: "Latency exceeded the configured limit",
                },
              ],
            },
          ],
        }}
      />,
    );
    await expect
      .element(screen.getByRole("status"))
      .toHaveTextContent("Required promotion gates passed.");
    await expect
      .element(screen.getByText("Not configured", { exact: true }))
      .toBeVisible();
    await expect
      .element(
        screen
          .getByRole("region", { name: "Performance limit results" })
          .getByText(/report only/),
      )
      .toBeVisible();
  });
  it("keeps legacy gate assessments unrecorded and does not invent separate passes", async () => {
    const screen = await setup(
      <QualityRunResults
        run={{
          ...run,
          status: "passed",
          promotion_eligible: true,
        }}
      />,
    );
    await expect
      .element(screen.getByRole("status"))
      .toHaveTextContent("Legacy run is promotion eligible");
    const gates = screen.getByRole("region", {
      name: "Promotion gate results",
    });
    expect(gates.getByText("Not recorded", { exact: true }).all()).toHaveLength(
      3,
    );
    await expect
      .element(gates.getByText("Passed", { exact: true }))
      .not.toBeInTheDocument();
  });
  it("does not claim promotion gates passed when required assessments are missing or unavailable", async () => {
    const screen = await setup(
      <QualityRunResults
        run={{
          ...run,
          status: "passed",
          promotion_eligible: true,
          gate_results: {
            mandatory_critical: {
              status: "passed",
              required: true,
              case_ids: [item.id],
            },
            other_quality: {
              status: "unavailable",
              required: true,
              case_ids: [],
              required_scorers: ["tool_selection"],
              mode: "report_only",
            },
          },
        }}
      />,
    );
    await expect
      .element(screen.getByRole("status"))
      .toHaveTextContent("Promotion gates have not passed.");
    await expect
      .element(screen.getByText("Not recorded", { exact: true }))
      .toBeVisible();
    await screen.rerender(
      <QualityRunResults
        run={{
          ...run,
          status: "passed",
          promotion_eligible: true,
          gate_results: {
            mandatory_critical: {
              status: "passed",
              required: true,
              case_ids: [item.id],
            },
            other_quality: {
              status: "not_configured",
              required: false,
              case_ids: [],
              required_scorers: [],
              mode: "report_only",
            },
          },
        }}
      />,
    );
    await expect
      .element(screen.getByRole("status"))
      .toHaveTextContent("Promotion gates have not passed.");
  });
  it("keeps pending modern assessments unavailable without treating the run as legacy", async () => {
    const pending: QualityRun = {
      ...run,
      status: "running",
      promotion_eligible: true,
      results: [],
      gates: {
        other_cases: "report_only",
        required_scorers: [],
        performance: "required",
        max_latency_ms: 0,
        count_budgets: {},
      },
    };
    const screen = await setup(<QualityRunResults run={pending} />);
    await expect
      .element(
        screen.getByText(
          "Gate assessments will appear as the evaluation completes.",
        ),
      )
      .toBeVisible();
    await expect
      .element(screen.getByRole("status"))
      .toHaveTextContent("Promotion gates have not passed.");
    await screen.rerender(
      <QualityRunResults run={{ ...pending, status: "unavailable" }} />,
    );
    await expect
      .element(
        screen.getByText("Gate assessments are unavailable for this run."),
      )
      .toBeVisible();
    await expect
      .element(screen.getByRole("status"))
      .toHaveTextContent("Promotion gates have not passed.");
  });
  it("creates a reviewed configuration draft without updating or publishing the active agent", async () => {
    const base = {
      agent_id: "sales",
      name: "Sales",
      config_revision: "revision-3",
      release_manifest_id: "manifest-active",
      instructions_response: "Use evidence",
      instructions_orchestration: "Check metric",
    };
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((input, init) => {
        const url = String(input);
        if (url.endsWith("/versions") && init?.method === "POST")
          return json({
            version_id: "new-draft",
            configuration: JSON.parse(init.body as string).configuration,
            label: "Draft",
            created_at: "2026-10-03T01:00:00Z",
          });
        if (url.endsWith("/agents/sales")) return json(base);
        if (url.endsWith("/manifest")) return json({ manifest: null });
        if (url.includes("/versions?"))
          return json({ versions: [], active_version_id: "active" });
        return json({
          version_id: "new-draft",
          configuration: {
            name: "Sales",
            instructions_response: "Cite governed evidence",
          },
        });
      });
    const screen = await setup(
      <QualityProposalDraft agentId="sales" epoch={0} onClose={vi.fn()} />,
    );
    await expect
      .element(screen.getByRole("button", { name: "Save configuration draft" }))
      .toBeDisabled();
    await screen
      .getByLabelText("Response instructions")
      .fill("Cite governed evidence");
    await screen
      .getByRole("button", { name: "Save configuration draft" })
      .click();
    await expect
      .element(screen.getByRole("button", { name: "Publish selected version" }))
      .toBeDisabled();
    const call = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(call![1]!.body as string)).toEqual({
      expected_revision: "revision-3",
      configuration: {
        name: "Sales",
        instructions_response: "Cite governed evidence",
        instructions_orchestration: "Check metric",
      },
    });
    expect(
      fetch.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(1);
  });
  it("evaluates the selected release, shows scorer gates, opens a trace and proposes analysis", async () => {
    const fetch = mock();
    const onTrace = vi.fn();
    const screen = await setup(
      <AgentQualityTab agentId="sales" onInspectTrace={onTrace} />,
    );
    await expect
      .element(
        screen.getByText("Legacy release: dependencies are unevaluated."),
      )
      .toBeVisible();
    await screen.getByRole("button", { name: "Evaluate release" }).click();
    await expect
      .element(screen.getByText("Unsupported numeric claim"))
      .toBeVisible();
    await expect
      .element(screen.getByText("Promotion gates have not passed."))
      .toBeVisible();
    await screen.getByRole("button", { name: "Inspect trace" }).click();
    expect(onTrace).toHaveBeenCalledWith("trace-1");
    await screen.getByRole("button", { name: "Analyze failures" }).click();
    await expect
      .element(
        screen.getByText(
          "A reviewable improvement proposal is available in Suggestions.",
        ),
      )
      .toBeVisible();
    const evaluation = fetch.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/quality/runs") && init?.method === "POST",
    );
    expect(JSON.parse(evaluation![1]!.body as string)).toEqual({
      version_id: "draft",
    });
  });
  it("creates regression cases with reviewed assertions and does not modify the frozen run", async () => {
    const fetch = mock();
    const screen = await setup(<AgentQualityTab agentId="sales" />);
    await screen.getByRole("button", { name: "Evaluate release" }).click();
    await screen
      .getByRole("button", { name: "Create regression case" })
      .click();
    await expect
      .element(screen.getByLabelText("Case name"))
      .toHaveValue("Revenue evidence regression");
    await screen.getByRole("button", { name: "Save evaluation case" }).click();
    await vi.waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/quality/cases") && init?.method === "POST",
        ),
      ).toBe(true),
    );
    const call = fetch.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/quality/cases") && init?.method === "POST",
    );
    expect(JSON.parse(call![1]!.body as string)).toMatchObject({
      name: "Revenue evidence regression",
      source: "regression",
      assertions: item.assertions,
      critical: true,
      mandatory: true,
    });
    expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
      false,
    );
  });
  it("rejects invalid JSON and non-object expectations before creating a case", async () => {
    const fetch = mock();
    const screen = await setup(<AgentQualityTab agentId="sales" />);
    await screen
      .getByRole("button", { name: "Create case", exact: true })
      .click();
    await screen.getByLabelText("Case name").fill("New");
    await screen.getByLabelText("User prompt").fill("Revenue?");
    await screen.getByLabelText("Expected values (JSON)").fill("{");
    await screen.getByRole("button", { name: "Save evaluation case" }).click();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("valid JSON");
    await screen.getByLabelText("Expected values (JSON)").fill("[]");
    await screen.getByRole("button", { name: "Save evaluation case" }).click();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("JSON object");
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(
      false,
    );
  });
  it("saves monitoring with the current revision and without supplying an execution binding", async () => {
    const fetch = mock();
    const screen = await setup(<AgentQualityTab agentId="sales" />);
    await screen.getByText("Production trace scoring", { exact: true }).click();
    await screen.getByLabelText("Enable production scoring").click();
    await screen
      .getByRole("button", { name: "Save monitoring settings" })
      .click();
    await vi.waitFor(() =>
      expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
        true,
      ),
    );
    const call = fetch.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(call![1]!.body as string)).toEqual({
      enabled: true,
      sample_rate: 0.1,
      max_traces: 20,
      cadence_minutes: 60,
      expected_revision: 3,
    });
  });
  it("distinguishes unavailable quality from success and retains Knowledge and Suggestions tabs", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
      String(input).includes("/quality")
        ? json({ detail: "Disabled" }, 503)
        : String(input).includes("readiness")
          ? json({ checks: [] })
          : json({ candidates: [], feedback: [], materialized_views: [] }),
    );
    const screen = await setup(<AgentImproveTab agentId="sales" />);
    await expect
      .element(screen.getByText(/Agent Quality is unavailable/))
      .toBeVisible();
    await screen.getByRole("tab", { name: "Knowledge" }).click();
    await expect
      .element(screen.getByRole("button", { name: "Review knowledge" }))
      .toBeVisible();
    await userEvent.keyboard("{ArrowRight}");
    await expect
      .element(screen.getByRole("tab", { name: "Suggestions" }))
      .toHaveAttribute("aria-selected", "true");
    await expect
      .element(screen.getByRole("heading", { name: "From usage" }))
      .toBeVisible();
  });
  it("labels diagnoses as hypotheses and sends revision-bound proposal review", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((_input, init) =>
        init?.method === "POST"
          ? json({ status: "accepted" })
          : json({
              items: [
                {
                  id: "proposal-1",
                  revision: 2,
                  status: "proposed",
                  diagnosis: ["WRONG_TOOL"],
                  hypothesis: true,
                  suggestion: "Add a regression case.",
                },
              ],
            }),
      );
    const screen = await setup(<QualitySuggestions agentId="sales" />);
    await expect
      .element(screen.getByText("Suspected causes: WRONG TOOL"))
      .toBeVisible();
    await screen.getByRole("button", { name: "Accept for draft" }).click();
    const call = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(String(call?.[0])).toContain("/quality/proposals/proposal-1/review");
    expect(JSON.parse(call![1]!.body as string)).toEqual({
      expected_revision: 2,
      resolution: "accepted",
    });
  });
  it("counts pass-to-unavailable regressions even when another case improves and marks changed case sets", async () => {
    const left = {
      ...run,
      results: [
        {
          ...run.results[0],
          scores: [{ ...run.results[0].scores[0], status: "pass" as const }],
        },
        { ...run.results[0], case_id: "case-2" },
      ],
    };
    const right = {
      ...run,
      results: [
        {
          ...run.results[0],
          scores: [
            { ...run.results[0].scores[0], status: "unavailable" as const },
          ],
        },
        {
          ...run.results[0],
          case_id: "case-2",
          scores: [{ ...run.results[0].scores[0], status: "pass" as const }],
        },
      ],
    };
    const screen = await setup(
      <QualityRunComparison left={left} right={right} />,
    );
    const row = screen.getByRole("row", { name: /numeric consistency/ });
    await expect
      .element(row.getByRole("cell").nth(0))
      .toHaveTextContent("1 / 1 / 0");
    await expect
      .element(row.getByRole("cell").nth(1))
      .toHaveTextContent("1 / 0 / 1");
    await expect.element(row.getByRole("cell").nth(2)).toHaveTextContent("1");
    await screen.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <QualityRunComparison
          left={left}
          right={{
            ...right,
            results: [{ ...right.results[0], case_revision: 2 }],
          }}
        />
      </QueryClientProvider>,
    );
    await expect
      .element(screen.getByRole("status"))
      .toHaveTextContent("case sets or revisions differ");
  });
  it("removes previous scoped results after a role change and handles narrow themes", async () => {
    await page.viewport(320, 800);
    const fetch = mock();
    const screen = await setup(<AgentQualityTab agentId="sales" />);
    await screen.getByText("Promotion gates", { exact: true }).click();
    await screen.getByLabelText("Maximum total tokens").fill("100");
    await screen.getByRole("button", { name: "Evaluate release" }).click();
    await expect
      .element(screen.getByText("Unsupported numeric claim"))
      .toBeVisible();
    for (const theme of ["", "dark"]) {
      document.documentElement.classList.toggle("dark", theme === "dark");
      for (const width of [320, 768, 1280]) {
        await page.viewport(width, 800);
        expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
      }
    }
    fetch.mockImplementation(() => json({ detail: "Role changed" }, 403));
    useAuthStore.getState().auth.setUser({
      username: "new-user",
      roles: ["ANALYST"],
      activeRole: "ANALYST",
    });
    await expect
      .element(screen.getByText("Unsupported numeric claim"))
      .not.toBeInTheDocument();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("Role changed");
  });
});
