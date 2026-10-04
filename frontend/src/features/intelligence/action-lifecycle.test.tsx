import { afterEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ActionLifecycle } from "./action-lifecycle";
import {
  AutomationActionPreview,
  MonitorActionPreview,
} from "./action-preview";
import type { BusinessAction, Decision } from "./lifecycle-api";
import "@/styles/index.css";

const action: Extract<BusinessAction, { adapter_id: "monitor-v1" }> = {
  id: "action-1",
  revision: 1,
  decision_id: "decision-1",
  decision_revision: 3,
  option_id: "option-1",
  adapter_id: "monitor-v1",
  status: "approved",
  expected_effect: "Create a metric monitor and schedule",
  request_digest: "digest",
  dispatch_attempts: 0,
  policy: {
    decision: "ALLOW",
    reason: "Monitor creation allowed",
    policy_revision: 1,
  },
  configuration: {
    name: "Revenue watch",
    agent_id: "sales",
    semantic: { view_id: "revenue", version: 1, fingerprint: "abc" },
    plan: { metrics: ["revenue"] },
    value_column: "revenue",
    count_column: "orders",
    time_dimension: "ordered_at",
    timezone: "UTC",
    enabled: true,
    cadence_minutes: 15,
  },
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
afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
  await page.viewport(1280, 800);
});

describe("governed action lifecycle", () => {
  it("reads an exact Mission Action pin and keeps historical consent inactive", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      json({
        ...action,
        status: "awaiting_consent",
        consent_call_id: "old-consent",
        execution_current: false,
      }),
    );
    const screen = await setup(
      <ActionLifecycle
        actionId="action-1"
        threadId="thread-1"
        missionId="mission-1"
        revision={3}
        canReview
      />,
    );
    await expect.element(screen.getByText(/Historical Action/)).toBeVisible();
    await expect
      .element(
        screen.getByRole("button", { name: "Allow this operation once" }),
      )
      .not.toBeInTheDocument();
    await expect
      .element(screen.getByRole("button", { name: "Cancel before dispatch" }))
      .not.toBeInTheDocument();
    expect(fetch.mock.calls[0][0]).toContain("mission_id=mission-1&revision=3");
  });
  it("switches action type by keyboard and reviews edited report configuration", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((_input, init) => {
        const body = init?.body ? JSON.parse(init.body as string) : null;
        return json({
          ...action,
          adapter_id: "automation-v1",
          configuration: body?.configuration ?? {
            agent_id: "sales",
            semantic: action.configuration.semantic,
            title: "Revenue by region",
            prompt: "Report weekly revenue by region.",
            schedule_kind: "cron",
            schedule_expr: "0 9 * * 1",
          },
        });
      });
    const screen = await setup(
      <MonitorActionPreview
        decision={
          {
            id: action.decision_id,
            revision: 3,
            selected_option_id: "option-1",
            title: "Revenue",
          } as Decision
        }
        configuration={action.configuration}
        threadId="thread-1"
        missionId="mission-1"
      />,
    );
    await screen.getByRole("radio", { name: "Metric monitor" }).click();
    await userEvent.keyboard("{ArrowRight}");
    await expect
      .element(screen.getByRole("radio", { name: "Scheduled Studio report" }))
      .toHaveFocus();
    await userEvent.keyboard(" ");
    await expect
      .element(screen.getByRole("radio", { name: "Scheduled Studio report" }))
      .toBeChecked();
    await screen.getByLabelText("Report title").fill("Revenue by region");
    await screen
      .getByLabelText("Question for each report")
      .fill("Report weekly revenue by region.");
    await screen.getByLabelText("Cron schedule").fill("0 9 * * 1");
    await screen
      .getByRole("button", { name: "Preview automation action" })
      .click();
    await expect
      .element(
        screen.getByRole("heading", { name: "Governed automation action" }),
      )
      .toBeVisible();
    const submitted = fetch.mock.calls.find(([url]) =>
      String(url).endsWith("/preview"),
    );
    expect(JSON.parse(submitted![1]!.body as string)).toMatchObject({
      adapter_id: "automation-v1",
      mission_id: "mission-1",
      configuration: {
        title: "Revenue by region",
        prompt: "Report weekly revenue by region.",
        schedule_expr: "0 9 * * 1",
        delivery: "studio",
      },
    });
  });
  it("renders automation configuration and readback without claiming business improvement", async () => {
    const automation: BusinessAction = {
      ...action,
      adapter_id: "automation-v1",
      compensation_receipt: null,
      status: "verification_required",
      dispatch_attempts: 1,
      expected_effect: "Create a scheduled agent report in Studio",
      configuration: {
        agent_id: "sales",
        semantic: action.configuration.semantic,
        title: "Weekly revenue report",
        prompt: "Summarize governed revenue for last week.",
        schedule_kind: "cron",
        schedule_expr: "0 8 * * 1",
        timezone: "Asia/Jakarta",
        enabled: true,
        delivery: "studio",
      },
      receipt: {
        automation_id: "automation-1",
        configuration_digest: "a".repeat(64),
        schedule_enabled: true,
        delivery: "studio",
      },
    };
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => json(automation));
    const screen = await setup(
      <ActionLifecycle actionId="action-1" threadId="thread-1" />,
    );
    await expect
      .element(
        screen.getByRole("heading", { name: "Governed automation action" }),
      )
      .toBeVisible();
    await expect
      .element(screen.getByText("Weekly revenue report", { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByText("Reports arrive in Studio history."))
      .toBeVisible();
    await expect
      .element(
        screen.getByText(
          /Business effects require a separate observation window/,
        ),
      )
      .toBeVisible();
    await expect
      .element(
        screen.getByRole("button", { name: "Request execution consent" }),
      )
      .not.toBeInTheDocument();
    await screen.getByRole("button", { name: "Verify by readback" }).click();
    expect(
      fetch.mock.calls.some(([url]) => String(url).endsWith("/verify")),
    ).toBe(true);
    await page.viewport(320, 800);
    for (const theme of ["", "dark"]) {
      document.documentElement.classList.toggle("dark", theme === "dark");
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
    }
  });

  it("previews an automation using the selected Decision and current Mission", async () => {
    const configuration = {
      agent_id: "sales",
      semantic: action.configuration.semantic,
      title: "Weekly revenue report",
      prompt: "Summarize governed revenue.",
      schedule_kind: "cron" as const,
      schedule_expr: "0 8 * * 1",
    };
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      json({
        ...action,
        adapter_id: "automation-v1",
        configuration,
      }),
    );
    const screen = await setup(
      <AutomationActionPreview
        decision={
          {
            id: action.decision_id,
            revision: 3,
            selected_option_id: "option-1",
          } as Decision
        }
        configuration={configuration}
        threadId="thread-1"
        missionId="mission-1"
      />,
    );
    await screen
      .getByRole("button", { name: "Preview automation action" })
      .click();
    await expect
      .element(
        screen.getByRole("heading", { name: "Governed automation action" }),
      )
      .toBeVisible();
    const submitted = fetch.mock.calls.find(([url]) =>
      String(url).endsWith("/preview"),
    );
    expect(JSON.parse(submitted![1]!.body as string)).toMatchObject({
      adapter_id: "automation-v1",
      configuration,
      decision_id: action.decision_id,
      expected_decision_revision: 3,
      option_id: "option-1",
      mission_id: "mission-1",
    });
  });
  it("keeps a pending execution request open while independently resolving tool consent", async () => {
    let current = action;
    let finish: ((value: Response) => void) | undefined;
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((input, init) => {
        const url = String(input);
        if (url.endsWith("/execute")) {
          current = {
            ...action,
            revision: 2,
            status: "awaiting_consent",
            consent_call_id: "call-1",
          };
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        }
        if (url.includes("/tool-calls/")) {
          current = {
            ...action,
            revision: 3,
            status: "verified",
            dispatch_attempts: 1,
            verification: {
              checked_at: "2026-10-03T01:00:00Z",
              complete: true,
              reason: "monitor_and_schedule_match",
            },
          };
          finish?.(
            new Response(JSON.stringify(current), {
              headers: { "Content-Type": "application/json" },
            }),
          );
          return json({ status: "approved" });
        }
        if (init?.method === "POST") return json(current);
        return json(current);
      });
    const screen = await setup(
      <ActionLifecycle actionId="action-1" threadId="thread-1" />,
    );
    await screen
      .getByRole("button", { name: "Request execution consent" })
      .click();
    await screen
      .getByRole("button", { name: "Allow this operation once" })
      .click();
    await expect
      .element(screen.getByRole("heading", { name: "Verification: complete" }))
      .toBeVisible();
    const consent = fetch.mock.calls.find(([url]) =>
      String(url).includes("/tool-calls/"),
    );
    expect(JSON.parse(consent![1]!.body as string)).toEqual({
      decision: "allow_once",
    });
    const execute = fetch.mock.calls.find(([url]) =>
      String(url).endsWith("/execute"),
    );
    expect(JSON.parse(execute![1]!.body as string)).toMatchObject({
      thread_id: "thread-1",
      expected_revision: 1,
    });
  });
  it("never displays execution after uncertain dispatch and uses readback verification", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      json({
        ...action,
        status: "verification_required",
        dispatch_attempts: 1,
      }),
    );
    const screen = await setup(
      <ActionLifecycle actionId="action-1" threadId="thread-1" />,
    );
    await expect
      .element(
        screen.getByRole("button", { name: "Request execution consent" }),
      )
      .not.toBeInTheDocument();
    await screen.getByRole("button", { name: "Verify by readback" }).click();
    expect(
      fetch.mock.calls.some(([url]) => String(url).endsWith("/verify")),
    ).toBe(true);
  });
  it("distinguishes reviewer permission from policy approval and submits current revision", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((_input, init) =>
        json(
          init?.method === "POST"
            ? { id: action.id, revision: 2, status: "approved" }
            : {
                ...action,
                status: "awaiting_approval",
                policy: { ...action.policy, decision: "REQUIRE_APPROVAL" },
              },
        ),
      );
    const screen = await setup(
      <ActionLifecycle actionId="action-1" threadId="thread-1" canReview />,
    );
    await screen
      .getByRole("button", { name: "Approve action as reviewer" })
      .click();
    const call = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(String(call![0])).toContain("/review");
    expect(JSON.parse(call![1]!.body as string)).toMatchObject({
      expected_revision: 1,
      operation: "approve",
    });
    await expect
      .element(
        screen.getByRole("button", { name: "Allow this operation once" }),
      )
      .not.toBeInTheDocument();
  });
  it("requires an explicit compensation preparation and new operation consent", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() =>
        json({ ...action, status: "verified", dispatch_attempts: 1 }),
      );
    const screen = await setup(
      <ActionLifecycle actionId="action-1" threadId="thread-1" />,
    );
    await expect
      .element(
        screen.getByRole("button", { name: "Request compensation consent" }),
      )
      .not.toBeInTheDocument();
    await screen.getByRole("button", { name: "Prepare compensation" }).click();
    await screen
      .getByRole("button", { name: "Request compensation consent" })
      .click();
    expect(
      fetch.mock.calls.some(
        ([url, init]) =>
          String(url).endsWith("/compensate") && init?.method === "POST",
      ),
    ).toBe(true);
  });
  it("preserves the original dispatch receipt while showing a distinct compensation readback", async () => {
    const receipt = {
      monitor_id: "monitor-1",
      monitor_revision: 7,
      task_id: "task-1",
      schedule_enabled: true,
    };
    let current: Extract<BusinessAction, { adapter_id: "monitor-v1" }> = {
      ...action,
      revision: 2,
      status: "compensation_required",
      dispatch_attempts: 1,
      compensation_attempts: 1,
      receipt,
    };
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((input, init) => {
        if (String(input).endsWith("/verify") && init?.method === "POST") {
          current = {
            ...current,
            revision: 3,
            status: "compensated",
            compensation_receipt: {
              ...receipt,
              monitor_revision: 8,
              schedule_enabled: false,
            },
            compensation: {
              checked_at: "2026-10-03T01:00:00Z",
              complete: true,
              reason: "monitor_and_schedule_disabled",
            },
          };
        }
        return json(current);
      });
    const screen = await setup(
      <ActionLifecycle actionId="action-1" threadId="thread-1" />,
    );
    const original = "Monitor monitor-1 · revision 7 · schedule enabled";
    await expect
      .element(screen.getByText(original, { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByRole("heading", { name: "Compensation readback" }))
      .not.toBeInTheDocument();
    await screen.getByRole("button", { name: "Verify by readback" }).click();
    const readbackHeading = screen.getByRole("heading", {
      name: "Compensation readback",
    });
    await expect.element(readbackHeading).toBeVisible();
    const readback = readbackHeading.element().parentElement!;
    await expect
      .element(readback)
      .toHaveTextContent("Monitor monitor-1 · revision 8 · schedule disabled");
    await expect.element(readback).not.toHaveTextContent(original);
    await expect
      .element(screen.getByText(original, { exact: true }))
      .toBeVisible();
    await expect
      .element(
        screen.getByText(
          "Compensation verified: monitor and schedule disabled",
        ),
      )
      .toBeVisible();
    const verification = fetch.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/verify") && init?.method === "POST",
    );
    expect(JSON.parse(verification![1]!.body as string)).toMatchObject({
      thread_id: "thread-1",
      expected_revision: 2,
    });
  });
  it("reuses preview identity on retry and preserves the selected decision revision", async () => {
    let failed = false;
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((input, _init) => {
        if (String(input).endsWith("/preview")) {
          if (!failed) {
            failed = true;
            return json({ detail: "Connection failed" }, 503);
          }
          return json(action);
        }
        return json(action);
      });
    const decision = {
      id: action.decision_id,
      revision: 3,
      selected_option_id: "option-1",
    } as Decision;
    const screen = await setup(
      <MonitorActionPreview
        decision={decision}
        configuration={action.configuration}
        threadId="thread-1"
      />,
    );
    await screen
      .getByRole("button", { name: "Preview monitor action" })
      .click();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("Connection failed");
    await screen
      .getByRole("button", { name: "Preview monitor action" })
      .click();
    await expect
      .element(screen.getByRole("heading", { name: "Governed monitor action" }))
      .toBeVisible();
    const bodies = fetch.mock.calls
      .filter(([url]) => String(url).endsWith("/preview"))
      .map(([, init]) => JSON.parse(init!.body as string));
    expect(bodies[0]).toEqual(bodies[1]);
    expect(bodies[0]).toMatchObject({
      expected_decision_revision: 3,
      option_id: "option-1",
      adapter_id: "monitor-v1",
    });
  });
  it("shows permission errors, omits unauthorized review, and reflows at narrow widths", async () => {
    await page.viewport(320, 800);
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      json({
        ...action,
        status: "awaiting_approval",
        policy: { ...action.policy, decision: "REQUIRE_APPROVAL" },
      }),
    );
    const screen = await setup(
      <ActionLifecycle actionId="action-1" threadId="thread-1" />,
    );
    await expect
      .element(screen.getByText(/Your current role cannot review it/))
      .toBeVisible();
    await expect
      .element(
        screen.getByRole("button", { name: "Approve action as reviewer" }),
      )
      .not.toBeInTheDocument();
    for (const theme of ["", "dark"]) {
      document.documentElement.classList.toggle("dark", theme === "dark");
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
    }
    fetch.mockImplementation(() => json({ detail: "Role changed" }, 403));
    await screen
      .getByRole("button", { name: "Cancel before dispatch" })
      .click();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("Role changed");
  });
});
