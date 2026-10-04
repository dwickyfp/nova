import { afterEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DecisionCompose } from "./decision-compose";
import { legacyUnitEconomics } from "./scenario-schema";
import type { Investigation, News } from "./lifecycle-api";
import "@/styles/index.css";

const news = {
  after: 20,
  window: { start: "2026-10-01T00:00:00Z", end: "2026-10-02T00:00:00Z" },
} as News;
const investigation = { id: "investigation-1" } as Investigation;
const json = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
const setup = (onCreated = vi.fn()) =>
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
        investigation={investigation}
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
  it("uses registered controls and keeps the existing decision wire format and retry identity", async () => {
    const onCreated = vi.fn();
    let first = true;
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((input, init) => {
        if (String(input).includes("/scenarios"))
          return json({ items: [legacyUnitEconomics] });
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
    expect(requests[0].options[0].simulation).toMatchObject({
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
      json({
        items: [
          {
            ...legacyUnitEconomics,
            input_schema: {
              type: "object",
              properties: { code: { type: "object" } },
            },
          },
        ],
      }),
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
  it("distinguishes permission errors from supported legacy fallback", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      json({ detail: "Permission changed" }, 403),
    );
    const screen = await setup();
    await screen.getByText("Prepare a decision", { exact: true }).click();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("Permission changed");
    await expect
      .element(screen.getByRole("button", { name: "Save decision options" }))
      .not.toBeInTheDocument();
  });
  it("reflows schema fields and add/remove options at 320px in both themes", async () => {
    await page.viewport(320, 800);
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      json({ detail: "Legacy" }, 404),
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
});
