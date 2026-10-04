import { useRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AgentMessage } from "@/features/agents/api";
import {
  AgentDoctor,
  DoctorFindings,
} from "@/features/agents/agent-detail/quality-doctor";
import { QualityProposalReview } from "@/features/agents/agent-detail/quality-proposal-review";
import {
  qualityApi,
  type DoctorReport,
  type QualityProposal,
} from "@/features/agents/quality-api";
import { ActionLifecycle } from "@/features/intelligence/action-lifecycle";
import { DecisionCompose } from "@/features/intelligence/decision-compose";
import {
  actionApi,
  intelligenceApi,
  type BusinessAction,
  type Investigation,
  type News,
} from "@/features/intelligence/lifecycle-api";
import { capacityScenario } from "@/features/intelligence/scenario-fixtures.test-support";
import { semanticViewsApi } from "@/features/intelligence/semantic-views-api";
import { api, ApiError } from "@/lib/api-client";
import { ContextPanel } from "./context-panel";
import { EvidencePanel } from "./evidence-panel";
import type { EvidenceEnvelope, EvidenceHealth } from "./evidence-health";
import { InvestigationStart } from "./investigation-start";
import { MissionObjects } from "./mission-objects";
import {
  MissionTurnControls,
  type MissionTurnChoice,
} from "./mission-turn-controls";
import {
  applyEvent,
  replayThread,
  type TranscriptTurn,
} from "./studio-transcript";
import { WorkflowRail } from "./workflow-rail";
import {
  workflowApi,
  type Mission,
  type MissionDeliverable,
} from "./workflow-api";
import "@/styles/index.css";

const semantic = {
  view_id: "sales",
  version: 2,
  fingerprint: "published-sales-v2",
};
const health: EvidenceHealth = {
  schema_version: 1,
  rule_version: "evidence-health-v1",
  assessed_at: "2026-10-04T10:00:00Z",
  label: "limited",
  facts: {
    semantic_grounding: "published",
    semantic_view_id: semantic.view_id,
    semantic_version: semantic.version,
    semantic_fingerprint: semantic.fingerprint,
    plan_source: "compiled",
    verified_query_hit: false,
    verified_query_id: null,
    execution_status: "success",
    coverage: "partial",
    semantic_ambiguity: "none",
    source_agreement: "unknown",
    causal_strength: "arithmetic",
    unsupported_numeric_claims: false,
    data_as_of: null,
    max_age_seconds: null,
    evidence_refs: ["evidence-1"],
  },
  data_freshness: {
    status: "unknown",
    data_as_of: null,
    age_seconds: null,
    max_age_seconds: null,
  },
  reasons: ["coverage_partial", "sample_count_unknown"],
  unknown_signals: ["data_freshness"],
};
const envelope: EvidenceEnvelope = {
  schema_version: 1,
  health,
  semantic,
  metrics: ["revenue"],
  dimensions: ["region"],
  current_window: {
    start: "2026-10-01T00:00:00Z",
    end: "2026-10-04T10:00:00Z",
  },
  baseline_window: {
    start: "2026-09-01T00:00:00Z",
    end: "2026-09-04T10:00:00Z",
  },
  timezone: "Asia/Jakarta",
  filter_shape: [],
  named_filters: [],
  warnings: ["sample_count_unknown"],
  evidence_refs: ["evidence-1"],
  validated_plan_fingerprint: "a".repeat(64),
  model_fingerprint: "model-v2",
};
const mission: Mission = {
  mission_id: "mission-1",
  thread_id: "thread-1",
  agent_id: "sales-agent",
  objective: "Investigate regional revenue",
  work_intent: "INVESTIGATE",
  status: "completed",
  revision: 7,
  run_ids: ["run-1"],
  stages: [
    {
      kind: "investigate",
      label: "Inspect regional contributions",
      status: "completed",
      source_refs: ["investigation-1"],
    },
  ],
  evidence_refs: envelope.evidence_refs,
  object_refs: [{ kind: "investigation", id: "investigation-1", revision: 3 }],
  cancel_requested: false,
  created_at: "2026-10-04T10:00:00Z",
  updated_at: "2026-10-04T10:00:00Z",
  continuation: {
    mode: "continue",
    reason: "semantic_anchor_match",
    mission_id: "mission-1",
  },
};
const investigation: Investigation = {
  id: "investigation-1",
  revision: 3,
  created_at: "2026-10-04T10:00:00Z",
  status: "complete",
  semantic,
  evidence: [],
  news_id: "news-1",
  residual: 0,
  decompositions: [],
  timeline: [],
  hypotheses: [
    {
      id: "region-1",
      label: "Regional arithmetic contribution",
      contribution: -10,
      causal_status: "arithmetic",
      next_test: "Compare authorized regional detail",
      evidence_ids: envelope.evidence_refs,
    },
  ],
};
const persistedReport: MissionDeliverable = {
  deliverable_id: "report-1",
  mission_id: mission.mission_id,
  mission_revision: 7,
  kind: "investigation_report",
  title: "Pinned investigation report",
  markdown:
    "# Recorded regional analysis\nThe recorded contribution is arithmetic. Sample count is unknown.",
  evidence_refs: envelope.evidence_refs,
  object_refs: mission.object_refs,
  created_at: "2026-10-04T10:00:00Z",
  sources: [
    {
      kind: "investigation",
      id: investigation.id,
      revision: 3,
      fingerprint: "b".repeat(64),
      semantic,
      facts: { causal_status: "arithmetic" },
    },
  ],
};
const messages: AgentMessage[] = [
  {
    message_id: "question-1",
    role: "user",
    content: "Investigate revenue",
    created_at: "2026-10-04T09:00:00Z",
  },
  {
    message_id: "answer-1",
    role: "assistant",
    content: "Recorded revenue changed.",
    created_at: "2026-10-04T10:00:00Z",
    steps: [
      {
        kind: "tool",
        name: "semantic_query",
        tool_call_id: "query-1",
        status: "done",
        arguments: {},
        workflow: {
          mission_id: mission.mission_id,
          run_id: "run-1",
          root_run_id: null,
        },
        trace_detail: { evidence_health: health, evidence_envelope: envelope },
      },
    ],
  },
] as AgentMessage[];
const replay = replayThread(messages);
const matrix = [
  { theme: "light", width: 360 },
  { theme: "dark", width: 360 },
  { theme: "light", width: 1440 },
  { theme: "dark", width: 1440 },
];
const clients: QueryClient[] = [];
function client() {
  const value = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(value);
  return value;
}
function wrap(node: React.ReactNode) {
  return (
    <QueryClientProvider client={client()}>
      <main className="min-w-0 w-full max-w-full p-3">{node}</main>
    </QueryClientProvider>
  );
}
async function viewport(theme: string, width: number) {
  document.documentElement.classList.toggle("dark", theme === "dark");
  await page.viewport(width, 900);
}
function keyboardActivate(element: Element) {
  (element as HTMLElement).focus();
  return userEvent.keyboard("{Enter}");
}
function assertContained(width: number) {
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
}
function ReplayHost({
  onFollowUp,
}: {
  onFollowUp: (prompt: string, missionId?: string) => void;
}) {
  const [available, setAvailable] = useState(false);
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<MissionTurnChoice>({
    mode: "automatic",
  });
  const opener = useRef<HTMLButtonElement>(null);
  return (
    <div className="flex h-dvh min-w-0 overflow-hidden">
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="shrink-0 p-3">
          Studio
          {available && (
            <button
              ref={opener}
              className="ml-2 min-h-11 rounded border px-3"
              onClick={() => setOpen(true)}
            >
              Open workflow details
            </button>
          )}
        </header>
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto p-3">
          <MissionTurnControls
            threadId={mission.thread_id}
            value={choice}
            onChange={setChoice}
            disabled={false}
          />
          <p className="mb-3 text-sm">{replay[0].answer}</p>
          <MissionObjects mission={mission} onFollowUp={onFollowUp} />
        </div>
      </main>
      <WorkflowRail
        threadId={mission.thread_id}
        turns={replay}
        streaming={false}
        agents={[]}
        runs={[]}
        runLoading={false}
        runError={false}
        retryRuns={vi.fn()}
        onSelectChild={vi.fn()}
        onAvailable={setAvailable}
        mobileOpen={open}
        onMobileOpenChange={(value) => {
          setOpen(value);
          if (!value) requestAnimationFrame(() => opener.current?.focus());
        }}
      />
    </div>
  );
}
beforeEach(() => {
  vi.spyOn(workflowApi, "list").mockResolvedValue({ missions: [mission] });
  vi.spyOn(workflowApi, "resumable").mockResolvedValue({ missions: [] });
  vi.spyOn(workflowApi, "deliverables").mockResolvedValue({
    deliverables: [persistedReport],
  });
  vi.spyOn(workflowApi, "canonical").mockResolvedValue(investigation);
});
afterEach(async () => {
  await cleanup();
  for (const value of clients.splice(0)) value.clear();
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
  await page.viewport(1280, 720);
});

describe("governed lifecycle browser surfaces", () => {
  it.each(matrix)(
    "replays pinned records and selects exact Context by keyboard ($theme, $width px)",
    async ({ theme, width }) => {
      await viewport(theme, width);
      const emptyTurn: TranscriptTurn = {
        id: "question-1",
        question: "Investigate revenue",
        steps: [],
        answer: "",
        content: [],
        pendingConsent: null,
        blocks: { tables: [], charts: [], citations: [] },
        state: "streaming",
      };
      const live = applyEvent([emptyTurn], "question-1", {
        type: "evidence_envelope",
        tool_call_id: "query-1",
        tool_name: "semantic_query",
        payload: envelope,
        workflow: {
          mission_id: mission.mission_id,
          run_id: "run-1",
          root_run_id: null,
        },
      });
      expect(replay[0].evidence).toEqual(live[0].evidence);
      const followup = vi.fn();
      const graph = {
        nodes: [
          {
            id: "exact-revenue",
            name: "Selected historical revenue",
            kind: "metric",
            reference_id: "sales:2:revenue",
            state: "CONFLICTED",
            semantic,
            authority: "published_semantic_definition",
            source_kind: "published_semantic",
            validity: "historical",
            freshness: "unknown",
            authority_basis: { version: 2 },
            contradictions: ["conflict-revenue"],
          },
        ],
        edges: [],
        bounded: true,
        conflicts: [
          {
            kind: "metric",
            term: "revenue",
            node_ids: ["exact-revenue", "conflict-revenue"],
            resolved: false,
          },
        ],
      };
      const resolve = vi.spyOn(api, "post").mockResolvedValue({
        status: "resolved",
        selected_metric: "revenue",
        node_id: "exact-revenue",
        selected_node: graph.nodes[0],
        semantic,
        graph,
      } as never);
      await render(
        <QueryClientProvider client={client()}>
          <ReplayHost onFollowUp={followup} />
        </QueryClientProvider>,
      );
      await expect
        .element(
          page.getByRole("heading", {
            name: "Regional arithmetic contribution",
          }),
        )
        .toBeVisible();
      const continuation = page.getByRole("button", {
        name: "Continue mission",
        exact: true,
      });
      await expect.element(continuation).toBeEnabled();
      await keyboardActivate(continuation.element());
      await expect.element(continuation).toHaveFocus();
      await expect
        .element(page.getByLabelText("Mission to continue"))
        .toHaveValue(mission.mission_id);
      await keyboardActivate(
        page
          .getByRole("button", { name: "Continue investigation", exact: true })
          .element(),
      );
      expect(followup.mock.calls[0][1]).toBe(mission.mission_id);
      if (width === 360) {
        await keyboardActivate(
          page.getByRole("button", { name: "Open workflow details" }).element(),
        );
        await expect.element(page.getByRole("dialog")).toBeVisible();
      }
      await page
        .getByRole("button", {
          name: "Pinned investigation report · revision 7",
        })
        .click();
      await expect
        .element(
          page.getByRole("heading", { name: "Recorded regional analysis" }),
        )
        .toBeVisible();
      await keyboardActivate(
        page.getByText("Source references", { exact: true }).element(),
      );
      await expect
        .element(
          page.getByText(
            /investigation: investigation-1 · revision 3 · fingerprint/,
          ),
        )
        .toBeVisible();
      const activity = page.getByRole("tab", { name: "Activity", exact: true });
      (activity.element() as HTMLElement).focus();
      await userEvent.keyboard("{ArrowRight}");
      await expect
        .element(page.getByRole("tab", { name: "Evidence", exact: true }))
        .toHaveFocus();
      await expect
        .element(page.getByText("Limited evidence", { exact: true }))
        .toBeVisible();
      await expect
        .element(page.getByText("Arithmetic contribution", { exact: true }))
        .toBeVisible();
      await expect
        .element(page.getByText("coverage partial", { exact: true }))
        .toBeVisible();
      await expect
        .element(page.getByText(/Current: 2026-10-01T00:00:00Z/))
        .toBeVisible();
      await keyboardActivate(
        page
          .getByRole("button", { name: "View revenue context", exact: true })
          .element(),
      );
      await expect
        .element(
          page.getByRole("region", { name: "Context authority and conflicts" }),
        )
        .toBeVisible();
      await expect
        .element(
          page.getByText("Unresolved definition conflicts", { exact: true }),
        )
        .toBeVisible();
      await expect
        .element(page.getByText("historical", { exact: true }))
        .toBeVisible();
      expect(resolve).toHaveBeenCalledWith(
        "/intelligence/context/resolve-metric",
        { semantic, term: "revenue", exact: true, include_context: true },
      );
      assertContained(width);
      await page.screenshot({
        path: `__screenshots__/lifecycle-context-${theme}-${width}.png`,
      });
      if (width === 360) {
        await userEvent.keyboard("{Escape}");
        await expect.element(page.getByRole("dialog")).not.toBeInTheDocument();
        await expect
          .element(page.getByRole("button", { name: "Open workflow details" }))
          .toHaveFocus();
      } else {
        expect(
          page
            .getByRole("complementary", { name: "Workflow details" })
            .element()
            .getBoundingClientRect().height,
        ).toBeLessThanOrEqual(900);
      }
    },
  );

  it.each(matrix)(
    "prefills missing Investigation inputs and shows honest count limits ($theme, $width px)",
    async ({ theme, width }) => {
      await viewport(theme, width);
      vi.spyOn(intelligenceApi, "page").mockResolvedValue({
        items: [],
        next_after: null,
      });
      vi.spyOn(semanticViewsApi, "get").mockResolvedValue({
        versions: [
          {
            version: 2,
            fingerprint: semantic.fingerprint,
            definition: {
              metrics: [
                {
                  name: "revenue",
                  default_time_dimension: "orders.ordered_at",
                },
              ],
              datasets: [
                {
                  name: "orders",
                  fields: [
                    { name: "ordered_at", dimension: { is_time: true } },
                  ],
                },
              ],
            },
          },
        ],
      } as never);
      const pendingMission: Mission = {
        ...mission,
        status: "blocked",
        investigation_requirements: {
          required_inputs: ["time_dimension"],
          established: envelope,
        },
      };
      await render(
        wrap(<InvestigationStart mission={pendingMission} refresh={vi.fn()} />),
      );
      await keyboardActivate(
        page
          .getByRole("button", { name: "Complete investigation setup" })
          .element(),
      );
      await expect
        .element(
          page.getByText(
            "Sample count remains unknown for this one-time comparison.",
          ),
        )
        .toBeVisible();
      await expect
        .element(page.getByLabelText("Time dimension", { exact: true }))
        .toHaveValue("orders.ordered_at");
      for (const name of [
        "Baseline start",
        "Baseline end",
        "Observed start",
        "Observed end",
      ]) {
        expect(
          (
            page
              .getByLabelText(name, { exact: true })
              .element() as HTMLInputElement
          ).value,
        ).not.toBe("");
      }
      await expect
        .element(
          page.getByRole("button", { name: "Run governed investigation" }),
        )
        .toBeEnabled();
      await expect
        .element(
          page.getByText(
            /Established: revenue · semantic version 2. Missing: time dimension/,
          ),
        )
        .toBeVisible();
      assertContained(width);
    },
  );

  it.each(matrix)(
    "renders a generic scenario and uncertain automation without redispatch ($theme, $width px)",
    async ({ theme, width }) => {
      await viewport(theme, width);
      vi.spyOn(intelligenceApi, "scenarios").mockResolvedValue({
        items: [capacityScenario],
        compatibility: [
          {
            scenario: capacityScenario,
            compatible: true,
            reason_codes: [],
            required_inputs: [],
            resolved: {
              target_metric: "orders",
              currency: null,
              unit: "orders",
              currency_readonly: false,
              currency_input_allowed: false,
            },
          },
        ],
      });
      const news = {
        id: "news-1",
        after: 20,
        window: envelope.current_window,
      } as News;
      const automation: BusinessAction = {
        id: "automation-action",
        revision: 4,
        decision_id: "decision-1",
        decision_revision: 2,
        option_id: "option-1",
        adapter_id: "automation-v1",
        status: "verification_required",
        expected_effect: "Schedule a Studio report",
        request_digest: "action-digest",
        dispatch_attempts: 1,
        policy: {
          decision: "ALLOW",
          reason: "Internal reporting allowed",
          policy_revision: 3,
        },
        configuration: {
          agent_id: "sales-agent",
          semantic,
          title: "Regional revenue report",
          prompt: "Summarize governed regional revenue.",
          schedule_kind: "cron",
          schedule_expr: "0 9 * * 1",
          delivery: "studio",
        },
        receipt: {
          automation_id: "report-schedule-1",
          configuration_digest: "c".repeat(64),
          schedule_enabled: true,
          delivery: "studio",
        },
      };
      vi.spyOn(actionApi, "get").mockResolvedValue(automation);
      const operate = vi.spyOn(actionApi, "operate").mockResolvedValue({
        ...automation,
        status: "verified",
        verification: {
          checked_at: "2026-10-04T10:00:00Z",
          complete: true,
          reason: "matched_configuration",
        },
      });
      await render(
        wrap(
          <div className="min-w-0 space-y-5">
            <DecisionCompose
              news={news}
              investigation={investigation}
              missionId={mission.mission_id}
              threadId={mission.thread_id}
              onCreated={vi.fn()}
            />
            <ActionLifecycle
              actionId={automation.id}
              threadId={mission.thread_id}
            />
          </div>,
        ),
      );
      await keyboardActivate(
        page.getByText("Prepare a decision", { exact: true }).element(),
      );
      await expect
        .element(
          page.getByLabelText("Observed order capacity *", { exact: true }),
        )
        .toBeVisible();
      await expect
        .element(
          page.getByLabelText("Additional order capacity *", { exact: true }),
        )
        .toBeVisible();
      await expect
        .element(page.getByLabelText("Published metric currency"))
        .not.toBeInTheDocument();
      await keyboardActivate(
        page.getByRole("button", { name: "Add option", exact: true }).element(),
      );
      await expect
        .element(
          page.getByRole("button", { name: "Remove option 2", exact: true }),
        )
        .toBeVisible();
      await keyboardActivate(
        page
          .getByRole("button", { name: "Remove option 2", exact: true })
          .element(),
      );
      await expect
        .element(page.getByText(/Dispatch outcome is uncertain/))
        .toBeVisible();
      await expect
        .element(
          page.getByRole("button", {
            name: "Request execution consent",
            exact: true,
          }),
        )
        .not.toBeInTheDocument();
      await expect
        .element(
          page.getByText(
            /Verification confirms automation configuration. Business effects require/,
          ),
        )
        .toBeVisible();
      const readback = page.getByRole("button", {
        name: "Verify by readback",
        exact: true,
      });
      await keyboardActivate(readback.element());
      await expect.poll(() => operate.mock.calls.length).toBe(1);
      expect(operate.mock.calls[0].slice(0, 2)).toEqual([
        automation.id,
        "verify",
      ]);
      expect(operate.mock.calls[0][2]).toMatchObject({
        thread_id: mission.thread_id,
        expected_revision: 4,
      });
      assertContained(width);
    },
  );

  it.each(matrix)(
    "keeps partial Doctor findings and reviewed patches distinct from publication ($theme, $width px)",
    async ({ theme, width }) => {
      await viewport(theme, width);
      const report: DoctorReport = {
        schema_version: 2,
        known_good: null,
        first_bad: null,
        current: null,
        requirements: [
          {
            code: "EVALUATION_REQUIRED",
            detail: "A compatible evaluated baseline is required.",
          },
        ],
        regressions: [],
        changed_dependencies: [],
        findings: [
          {
            category: "SEMANTIC_ALIAS_COLLISION",
            hypothesis: true,
            detail: "The revenue alias may select a different definition.",
          },
        ],
        patches: [],
        regression_candidates: [],
      };
      const proposal: QualityProposal = {
        id: "proposal-1",
        revision: 2,
        status: "proposed",
        patches: [
          {
            id: "patch-1",
            kind: "append_instruction",
            description: "Require exact governed evidence",
            base_revision: "agent-revision-3",
            fields: ["instructions_response"],
            instruction: "Use the selected published metric version.",
            hypothesis: true,
          },
        ],
        regression_candidates: [
          {
            id: "case-1",
            run_id: "evaluation-1",
            case_id: "revenue-selection",
            case_revision: 2,
            scorers: ["semantic_selection"],
            review_required: true,
          },
        ],
      };
      const review = vi.fn();
      await render(
        wrap(
          <div className="min-w-0 space-y-5">
            <DoctorFindings report={report} />
            <QualityProposalReview
              proposal={proposal}
              busy={false}
              onReview={review}
            />
          </div>,
        ),
      );
      await expect
        .element(page.getByText("A compatible evaluated baseline is required."))
        .toBeVisible();
      await expect
        .element(page.getByText(/^Hypothesis: The revenue alias/))
        .toBeVisible();
      await expect
        .element(page.getByText("Use the selected published metric version."))
        .toBeVisible();
      const regression = page.getByRole("checkbox");
      (regression.element() as HTMLElement).focus();
      await userEvent.keyboard(" ");
      await expect.element(regression).toBeChecked();
      await userEvent.keyboard("{Tab}{Tab}{Enter}");
      expect(review).toHaveBeenCalledWith("accepted", {
        patch_id: "patch-1",
        regression_case_ids: ["case-1"],
      });
      await expect
        .element(page.getByText(/Evaluate the result before publication/))
        .toBeVisible();
      assertContained(width);
    },
  );

  it.each(matrix)(
    "announces loading before controlled API responses arrive ($theme, $width px)",
    async ({ theme, width }) => {
      await viewport(theme, width);
      let resolveAction!: (value: BusinessAction) => void;
      let resolveScenarios!: (value: {
        items: (typeof capacityScenario)[];
        compatibility: [];
      }) => void;
      let resolveDoctor!: (value: DoctorReport) => void;
      vi.spyOn(actionApi, "get").mockReturnValue(
        new Promise((resolve) => {
          resolveAction = resolve;
        }),
      );
      vi.spyOn(intelligenceApi, "scenarios").mockReturnValue(
        new Promise((resolve) => {
          resolveScenarios = resolve;
        }),
      );
      vi.spyOn(qualityApi, "doctor").mockReturnValue(
        new Promise((resolve) => {
          resolveDoctor = resolve;
        }),
      );
      await render(
        wrap(
          <div className="space-y-5">
            <ActionLifecycle
              actionId="loading-action"
              threadId={mission.thread_id}
            />
            <DecisionCompose
              news={{ window: envelope.current_window } as News}
              investigation={investigation}
              onCreated={vi.fn()}
            />
            <AgentDoctor agentId="sales-agent" epoch={0} />
          </div>,
        ),
      );
      await keyboardActivate(
        page.getByText("Prepare a decision", { exact: true }).element(),
      );
      await expect
        .element(page.getByText("Loading action and its policy…"))
        .toBeVisible();
      await expect
        .element(page.getByText("Loading registered scenarios…"))
        .toBeVisible();
      await expect
        .element(page.getByText("Loading release diagnosis…"))
        .toBeVisible();
      resolveScenarios({ items: [], compatibility: [] });
      resolveDoctor({
        schema_version: 2,
        known_good: null,
        first_bad: null,
        current: null,
        requirements: [],
        findings: [],
        regressions: [],
        changed_dependencies: [],
        patches: [],
        regression_candidates: [],
      });
      resolveAction({
        id: "loading-action",
        revision: 1,
        decision_id: "decision-1",
        decision_revision: 1,
        option_id: "option-1",
        adapter_id: "automation-v1",
        status: "denied",
        expected_effect: "Studio report",
        request_digest: "digest",
        dispatch_attempts: 0,
        policy: {
          decision: "DENY",
          reason: "Current policy denies reporting",
          policy_revision: 2,
        },
        configuration: {
          agent_id: "sales-agent",
          semantic,
          title: "Denied report",
          prompt: "Revenue",
          schedule_kind: "cron",
          schedule_expr: "0 9 * * 1",
        },
      });
      await expect
        .element(
          page.getByText(
            /No registered scenarios are compatible with this investigation/,
          ),
        )
        .toBeVisible();
      await expect
        .element(page.getByText("Current policy denies reporting"))
        .toBeVisible();
      await expect
        .element(
          page.getByRole("button", { name: "Request execution consent" }),
        )
        .not.toBeInTheDocument();
      assertContained(width);
    },
  );

  it.each(matrix)(
    "hides revoked evidence and preserves current-scope permission refusals ($theme, $width px)",
    async ({ theme, width }) => {
      await viewport(theme, width);
      vi.mocked(workflowApi.canonical).mockRejectedValue(
        new ApiError(403, "Investigation access revoked"),
      );
      vi.mocked(workflowApi.list).mockResolvedValue({ missions: [] });
      vi.mocked(workflowApi.resumable).mockResolvedValue({
        missions: [{ ...mission, resume_required: true }],
      });
      vi.spyOn(workflowApi, "resume").mockRejectedValue(
        new ApiError(403, "Current Semantic View access revoked"),
      );
      vi.spyOn(api, "post").mockRejectedValue(
        new ApiError(403, "Context access revoked"),
      );
      vi.spyOn(actionApi, "get").mockRejectedValue(
        new ApiError(403, "Action access revoked"),
      );
      vi.spyOn(intelligenceApi, "scenarios").mockRejectedValue(
        new ApiError(403, "Scenario access revoked"),
      );
      vi.spyOn(qualityApi, "doctor").mockRejectedValue(
        new ApiError(403, "Release history access revoked"),
      );
      const choice = vi.fn();
      await render(
        wrap(
          <div className="space-y-5">
            <MissionTurnControls
              threadId={mission.thread_id}
              value={{ mode: "automatic" }}
              onChange={choice}
              disabled={false}
            />
            <MissionObjects mission={mission} onFollowUp={vi.fn()} />
            <ContextPanel semantic={semantic} metric="revenue" />
            <ActionLifecycle
              actionId="denied-action"
              threadId={mission.thread_id}
            />
            <DecisionCompose
              news={{ window: envelope.current_window } as News}
              investigation={investigation}
              onCreated={vi.fn()}
            />
            <AgentDoctor agentId="sales-agent" epoch={0} />
            <EvidencePanel evidence={[]} />
          </div>,
        ),
      );
      await keyboardActivate(
        page.getByText("Prepare a decision", { exact: true }).element(),
      );
      await keyboardActivate(
        page
          .getByRole("button", { name: "Resume mission", exact: true })
          .element(),
      );
      await expect
        .element(page.getByText("Current Semantic View access revoked"))
        .toBeVisible();
      await expect
        .element(page.getByText("Investigation access revoked"))
        .toBeVisible();
      await expect
        .element(
          page.getByText(/Context is unavailable or its definition changed/),
        )
        .toBeVisible();
      await expect
        .element(
          page.getByText(/Action could not be loaded: Action access revoked/),
        )
        .toBeVisible();
      await expect
        .element(
          page.getByText(
            /Registered scenarios could not be loaded: Scenario access revoked/,
          ),
        )
        .toBeVisible();
      await expect
        .element(
          page.getByText(
            /Release diagnosis could not be loaded: Release history access revoked/,
          ),
        )
        .toBeVisible();
      await expect
        .element(
          page.getByRole("heading", {
            name: "Regional arithmetic contribution",
          }),
        )
        .not.toBeInTheDocument();
      await expect
        .element(
          page.getByRole("region", { name: "Context authority and conflicts" }),
        )
        .not.toBeInTheDocument();
      await expect
        .element(
          page.getByRole("button", { name: "Continue mission", exact: true }),
        )
        .toBeDisabled();
      await expect
        .element(page.getByText("Evidence has not been assessed"))
        .toBeVisible();
      expect(choice).not.toHaveBeenCalled();
      assertContained(width);
    },
  );
});
