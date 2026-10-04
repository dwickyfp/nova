import { afterEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentDoctor, DoctorFindings } from "./quality-doctor";
import { QualityProposalReview } from "./quality-proposal-review";
import { QualitySuggestions } from "./quality-tab";
import type { DoctorReport, QualityProposal } from "../quality-api";
import "@/styles/index.css";

const release = (id: string) => ({
  id,
  version_id: `release-${id}`,
  manifest_id: `manifest-${id}`,
  manifest_fingerprint: "fp",
  created_at: "2026-10-04",
});
const report: DoctorReport = {
  schema_version: 2,
  known_good: release("good"),
  first_bad: release("first-bad"),
  current: release("current"),
  regressions: [
    {
      case_id: "revenue",
      case_revision: 3,
      scorer: "semantic_selection",
      scorer_version: "1",
      after_trace_id: "regressed-trace",
      before: "pass",
      after: "fail",
      measurements: [{ name: "provider_calls", before: 2, after: 4, delta: 2 }],
      hypothesis: false,
    },
  ],
  changed_dependencies: [
    {
      category: "configuration",
      fields: ["instructions_response"],
      hypothesis: true,
    },
  ],
  findings: [
    {
      category: "SEMANTIC_ALIAS_COLLISION",
      detail:
        "Ambiguous aliases may contribute to metric selection regressions.",
      hypothesis: true,
    },
  ],
  requirements: [],
  patches: [],
  regression_candidates: [],
};
const proposal: QualityProposal = {
  id: "proposal",
  revision: 2,
  status: "proposed",
  patches: [
    {
      id: "patch-one",
      kind: "append_instruction",
      description: "Require governed evidence",
      base_revision: "base-3",
      fields: ["instructions_response"],
      instruction: "Cite governed evidence before concluding.",
      hypothesis: true,
    },
  ],
  regression_candidates: [
    {
      id: "candidate",
      run_id: "run-1",
      case_id: "revenue",
      case_revision: 3,
      scorers: ["semantic_selection"],
      review_required: true,
    },
  ],
};
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
const json = (value: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );

afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
  await page.viewport(1280, 800);
});

describe("Doctor and reviewed applications", () => {
  it.each([false, true])(
    "keeps release evidence readable on a narrow viewport, dark=%s",
    async (dark) => {
      document.documentElement.classList.toggle("dark", dark);
      await page.viewport(390, 844);
      const screen = await setup(<DoctorFindings report={report} />);
      await expect.element(screen.getByText("release-good")).toBeVisible();
      await expect.element(screen.getByText("release-first-bad")).toBeVisible();
      await expect.element(screen.getByText("release-current")).toBeVisible();
      await expect
        .element(screen.getByText(/Hypothesis: Ambiguous aliases/))
        .toBeVisible();
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
        window.innerWidth,
      );
      await expect.element(screen.getByRole("table")).toBeVisible();
      expect(
        screen.getByRole("table").element().parentElement!.scrollWidth,
      ).toBeGreaterThanOrEqual(
        screen.getByRole("table").element().parentElement!.clientWidth,
      );
    },
  );
  it("reviews concrete patch and optional frozen regression cases using the keyboard", async () => {
    const onReview = vi.fn();
    const screen = await setup(
      <QualityProposalReview
        proposal={proposal}
        busy={false}
        onReview={onReview}
      />,
    );
    await expect
      .element(screen.getByText(proposal.patches![0].instruction!))
      .toBeVisible();
    const checkbox = screen.getByRole("checkbox");
    (checkbox.element() as HTMLElement).focus();
    await userEvent.keyboard(" ");
    await userEvent.keyboard("{Tab}{Tab}{Enter}");
    expect(onReview).toHaveBeenCalledWith("accepted", {
      patch_id: "patch-one",
      regression_case_ids: ["candidate"],
    });
    await screen.rerender(
      <QualityProposalReview proposal={proposal} busy onReview={onReview} />,
    );
    await expect
      .element(screen.getByRole("button", { name: "Applying reviewed patch…" }))
      .toBeDisabled();
  });
  it("opens the exact recorded regressed trace", async () => {
    const onInspectTrace = vi.fn();
    const screen = await setup(
      <DoctorFindings report={report} onInspectTrace={onInspectTrace} />,
    );
    await screen
      .getByRole("button", { name: "Inspect regressed trace" })
      .click();
    expect(onInspectTrace).toHaveBeenCalledWith("regressed-trace");
  });
  it("retries pending application with its original review revision and stable inputs", async () => {
    const pending: QualityProposal = {
      ...proposal,
      revision: 3,
      status: "accepted",
      review_inputs: {
        revision: 2,
        resolution: "accepted",
        patch_id: "patch-one",
        regression_case_ids: ["candidate"],
      },
      application: {
        operation_id: "stable-operation",
        status: "pending",
        kind: "agent_draft",
        patch_id: "patch-one",
        base_revision: "base-3",
      },
    };
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((_input, init) =>
        init?.method === "POST"
          ? json({
              ...pending,
              revision: 4,
              application: {
                ...pending.application,
                status: "applied",
                version_id: "draft-1",
              },
            })
          : json({ items: [pending] }),
      );
    const screen = await setup(<QualitySuggestions agentId="sales" />);
    await screen
      .getByRole("button", { name: "Resume reviewed application" })
      .click();
    const request = fetch.mock.calls.find(
      ([, init]) => init?.method === "POST",
    );
    expect(JSON.parse(request![1]!.body as string)).toEqual({
      expected_revision: 2,
      resolution: "accepted",
      patch_id: "patch-one",
      regression_case_ids: ["candidate"],
    });
  });
  it("shows partial requirements and retries refused diagnosis without claiming a baseline", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementationOnce(() => json({ detail: "Permission denied" }, 403))
      .mockImplementation(() =>
        json({
          ...report,
          known_good: null,
          first_bad: null,
          regressions: [],
          requirements: [
            {
              code: "COMPATIBLE_BASELINE_REQUIRED",
              detail:
                "No earlier passing evaluation has compatible cases, scorers, and gates.",
            },
          ],
        }),
      );
    const screen = await setup(<AgentDoctor agentId="sales" epoch={0} />);
    await expect.element(screen.getByRole("alert")).toBeVisible();
    await screen.getByRole("button", { name: "Retry diagnosis" }).click();
    await expect
      .element(
        screen.getByText(
          "No earlier passing evaluation has compatible cases, scorers, and gates.",
        ),
      )
      .toBeVisible();
    expect(fetch).toHaveBeenCalledTimes(2);
    await expect
      .element(screen.getByText("Not established").first())
      .toBeVisible();
  });
});
