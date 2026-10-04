import { afterEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DecisionCompose } from "./decision-compose";
import {
  legacyUnitEconomics,
  type ScenarioDefinition,
  type ScenarioDiscovery,
} from "./scenario-schema";
import { capacityScenario } from "./scenario-fixtures.test-support";
import type { Investigation, News } from "./lifecycle-api";
import "@/styles/index.css";

const news = {
  after: 20,
  window: { start: "2026-10-01T00:00:00Z", end: "2026-10-02T00:00:00Z" },
} as News;
const investigation = { id: "investigation-1", revision: 1 } as Investigation;
const discovery = (
  ...definitions: ScenarioDefinition[]
): ScenarioDiscovery => ({
  items: definitions,
  compatibility: definitions.map((scenario) => ({
    scenario,
    compatible: true,
    reason_codes: [],
    required_inputs: [
      ...(scenario.shared_input_schema.required ?? []),
      ...(scenario.input_schema.required ?? []),
    ],
    resolved: {
      target_metric: scenario.target_metric ?? "revenue",
      currency: scenario.currency_required === false ? null : "USD",
      unit: scenario.currency_required === false ? "units" : "currency",
      currency_readonly: scenario.currency_required !== false,
      currency_input_allowed: false,
    },
  })),
});
const json = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
const setup = (
  onCreated = vi.fn(),
  selected = investigation,
  missionId?: string,
) =>
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
      <DecisionCompose
        news={news}
        investigation={selected}
        missionId={missionId}
        onCreated={onCreated}
      />
    </QueryClientProvider>,
  );

afterEach(async () => {
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
  await page.viewport(1280, 800);
});

describe("registered scenario decision composition", () => {
  it("uses registered controls and preserves retry identity with canonical scenario parameters", async () => {
    const onCreated = vi.fn();
    let first = true;
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((input, init) => {
        if (String(input).includes("/scenarios"))
          return json(discovery(legacyUnitEconomics));
        if (init?.method === "POST") {
          if (first) {
            first = false;
            return json({ detail: "Try again" }, 503);
          }
          return json({ id: "decision-1" });
        }
        return json({});
      });
    const screen = await setup(onCreated);
    await screen.getByText("Prepare a decision", { exact: true }).click();
    await expect
      .element(screen.getByLabelText("Published metric currency"))
      .toHaveValue("USD");
    await expect
      .element(screen.getByLabelText("Published metric currency"))
      .toHaveAttribute("readonly");
    for (const [label, value] of [
      ["Decision title", "Monitor revenue"],
      ["Outcome period starts", "2026-10-03T09:00"],
      ["Baseline units *", "10"],
      ["Unit price *", "2"],
      ["Unit cost *", "1"],
      ["Capacity in the outcome period *", "20"],
      ["Maximum action budget *", "100"],
      ["Description", "Move inventory"],
      ["Expected change in units *", "2"],
      ["Unit sensitivity, ± *", "0"],
      ["Action cost *", "0"],
    ])
      await screen.getByLabelText(label, { exact: true }).fill(value);
    await screen.getByRole("button", { name: "Save decision options" }).click();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("Try again");
    await screen.getByRole("button", { name: "Save decision options" }).click();
    await vi.waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith("decision-1"),
    );
    const requests = fetch.mock.calls
      .filter(([, init]) => init?.method === "POST")
      .map(([, init]) => JSON.parse(init!.body as string));
    expect(requests[0]).toEqual(requests[1]);
    expect(requests[0].currency).toBe("USD");
    expect(requests[0].investigation_revision).toBe(1);
    const discoveryUrl = new URL(
      String(fetch.mock.calls[0][0]),
      location.origin,
    );
    expect(discoveryUrl.searchParams.get("investigation_id")).toBe(
      investigation.id,
    );
    expect(discoveryUrl.searchParams.get("investigation_revision")).toBe("1");
    expect(requests[0].options[0].scenario_kind).toBe("unit-economics");
    expect(requests[0].options[0].scenario_version).toBe(1);
    expect(requests[0].options[0].parameters).toMatchObject({
      baseline_units: 10,
      price: 2,
      action_type: "inventory_transfer",
      discount: 0,
    });
    expect(
      new Date(requests[0].outcome_window.end).getTime() -
        new Date(requests[0].outcome_window.start).getTime(),
    ).toBe(86400000);
  });
  it("shows a safe error for unsupported schema fields", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      json(
        discovery({
          ...legacyUnitEconomics,
          input_schema: {
            type: "object",
            properties: { code: { type: "object" } },
          },
        } as unknown as ScenarioDefinition),
      ),
    );
    const screen = await setup();
    await screen.getByText("Prepare a decision", { exact: true }).click();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("unsupported controls");
    await expect
      .element(screen.getByRole("button", { name: "Save decision options" }))
      .not.toBeInTheDocument();
  });
  it("persists a second adapter through generic fields without currency or profit assumptions", async () => {
    const onCreated = vi.fn();
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((_, init) =>
        init?.method === "POST"
          ? json({ id: "capacity-decision" })
          : json(discovery(capacityScenario)),
      );
    const screen = await setup(onCreated);
    await screen.getByText("Prepare a decision", { exact: true }).click();
    for (const [label, value] of [
      ["Decision title", "Add order capacity"],
      ["Outcome period starts", "2026-10-03T09:00"],
      ["Observed order capacity *", "20"],
      ["Additional order capacity *", "10"],
      ["Description", "Increase capacity"],
    ])
      await screen.getByLabelText(label, { exact: true }).fill(value);
    await expect
      .element(screen.getByLabelText("Published metric currency"))
      .not.toBeInTheDocument();
    await screen.getByRole("button", { name: "Save decision options" }).click();
    await vi.waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith("capacity-decision"),
    );
    const [, init] = fetch.mock.calls.find(
      ([, request]) => request?.method === "POST",
    )!;
    const body = JSON.parse(init!.body as string);
    expect(body.currency).toBeUndefined();
    expect(body.options[0]).toMatchObject({
      scenario_kind: "capacity",
      scenario_version: 1,
      parameters: {
        action_type: "capacity_upgrade",
        baseline: 20,
        additional_capacity: 10,
        action_cost: 0,
      },
    });
    expect(body.options[0].simulation).toBeUndefined();
  });
  it.each([403, 404, 409])(
    "keeps contextual permission and revision errors closed (%s)",
    async (status) => {
      vi.spyOn(globalThis, "fetch").mockImplementation(() =>
        json({ detail: "Permission changed" }, status),
      );
      const screen = await setup();
      await screen.getByText("Prepare a decision", { exact: true }).click();
      await expect
        .element(screen.getByRole("alert"))
        .toHaveTextContent("Permission changed");
      await expect
        .element(screen.getByRole("button", { name: "Save decision options" }))
        .not.toBeInTheDocument();
    },
  );
  it("reflows schema fields and add/remove options at 320px in both themes", async () => {
    await page.viewport(320, 800);
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      json(discovery(legacyUnitEconomics)),
    );
    const screen = await setup();
    await screen.getByText("Prepare a decision", { exact: true }).click();
    await screen.getByRole("button", { name: "Add option" }).click();
    await expect
      .element(screen.getByRole("button", { name: "Remove option 2" }))
      .toBeVisible();
    await screen.getByRole("button", { name: "Remove option 2" }).click();
    for (const theme of ["", "dark"]) {
      document.documentElement.classList.toggle("dark", theme === "dark");
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
    }
  });
  it("shows missing published currency without creating an IDR input", async () => {
    const result = discovery(legacyUnitEconomics);
    result.items = [];
    result.compatibility![0] = {
      ...result.compatibility![0],
      compatible: false,
      reason_codes: ["METRIC_CURRENCY_REQUIRED"],
      required_inputs: ["currency"],
      resolved: {
        ...result.compatibility![0].resolved,
        currency: null,
        currency_readonly: false,
      },
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json(result));
    const screen = await setup();
    await screen.getByText("Prepare a decision", { exact: true }).click();
    await expect
      .element(
        screen.getByText("The target metric needs a published currency.", {
          exact: false,
        }),
      )
      .toBeVisible();
    await expect
      .element(screen.getByLabelText("Published metric currency"))
      .not.toBeInTheDocument();
    await expect
      .element(screen.getByRole("button", { name: "Save decision options" }))
      .not.toBeInTheDocument();
  });
  it("does not trust a catalog-only response for canonical Decision discovery", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      json({ items: [legacyUnitEconomics] }),
    );
    const screen = await setup();
    await screen.getByText("Prepare a decision", { exact: true }).click();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("does not report scenario compatibility");
    await expect
      .element(screen.getByRole("button", { name: "Save decision options" }))
      .not.toBeInTheDocument();
  });
  it("permits currency entry only when the reviewed adapter permits it", async () => {
    const result = discovery(legacyUnitEconomics);
    result.compatibility![0].resolved = {
      ...result.compatibility![0].resolved,
      currency: null,
      currency_readonly: false,
      currency_input_allowed: true,
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json(result));
    const screen = await setup();
    await screen.getByText("Prepare a decision", { exact: true }).click();
    const input = screen.getByLabelText("Published metric currency");
    await expect.element(input).toHaveValue("");
    await expect.element(input).not.toHaveAttribute("readonly");
    await input.fill("EUR");
    await expect.element(input).toHaveValue("EUR");
  });
  it("sends the Mission and exact Investigation revision in contextual discovery", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => json(discovery(legacyUnitEconomics)));
    const screen = await setup(
      vi.fn(),
      { ...investigation, revision: 7 },
      "mission-a",
    );
    await screen.getByText("Prepare a decision", { exact: true }).click();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    const url = new URL(String(fetch.mock.calls[0][0]), location.origin);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      investigation_id: investigation.id,
      investigation_revision: "7",
      mission_id: "mission-a",
    });
  });
});
