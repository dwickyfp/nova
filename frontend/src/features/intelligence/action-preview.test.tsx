import { afterEach, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MonitorActionPreview } from "./action-preview";
import {
  actionApi,
  intelligenceApi,
  type Decision,
  type MonitorConfiguration,
} from "./lifecycle-api";
import "@/styles/index.css";

const configuration: MonitorConfiguration = {
  name: "Revenue",
  agent_id: "finance",
  semantic: { view_id: "sales", version: 1, fingerprint: "published" },
  plan: {},
  value_column: "revenue",
  time_dimension: "sales.date",
};
const decision = {
  id: "decision",
  revision: 3,
  selected_option_id: "option",
} as Decision;
const json = (body: unknown) =>
  Promise.resolve(
    new Response(JSON.stringify(body), {
      headers: { "Content-Type": "application/json" },
    }),
  );
function mount(config = configuration) {
  return render(
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
      <MonitorActionPreview
        decision={decision}
        configuration={config}
        threadId="thread"
      />
    </QueryClientProvider>,
  );
}

afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
  await page.viewport(1280, 800);
});

it.each(["UTC", "America/New_York"])(
  "uses the server configured %s timezone for scheduled reports",
  async (timezone) => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() =>
        json({ runtime: { execution_timezone: timezone } }),
      );
    const preview = vi
      .spyOn(actionApi, "preview")
      .mockRejectedValue(new Error("Preview recorded"));
    const screen = await mount();
    await screen
      .getByRole("radio", { name: "Scheduled Studio report" })
      .click();
    await expect
      .element(screen.getByLabelText("Timezone", { exact: true }))
      .toHaveValue(timezone);
    await screen
      .getByRole("button", { name: "Preview automation action" })
      .click();
    await vi.waitFor(() => expect(preview).toHaveBeenCalled());
    expect(preview.mock.calls[0][0]).toMatchObject({
      adapter_id: "automation-v1",
      configuration: { timezone },
      expected_decision_revision: 3,
    });
    expect(String(fetch.mock.calls[0][0])).toContain(
      "/agents/studio/capabilities",
    );
  },
);

it("preserves explicit object timezone and a user's explicit override", async () => {
  const runtime = vi.spyOn(intelligenceApi, "executionTimezone");
  const preview = vi
    .spyOn(actionApi, "preview")
    .mockRejectedValue(new Error("Preview recorded"));
  const screen = await mount({ ...configuration, timezone: "Europe/Berlin" });
  await screen.getByRole("radio", { name: "Scheduled Studio report" }).click();
  await expect
    .element(screen.getByLabelText("Timezone", { exact: true }))
    .toHaveValue("Europe/Berlin");
  await screen.getByLabelText("Timezone", { exact: true }).fill("UTC");
  await screen
    .getByRole("button", { name: "Preview automation action" })
    .click();
  await vi.waitFor(() => expect(preview).toHaveBeenCalled());
  expect(preview.mock.calls[0][0].configuration.timezone).toBe("UTC");
  expect(runtime).not.toHaveBeenCalled();
});

it.each(["Europe/Berlin", undefined])(
  "keeps a report override separate from monitor timezone %s",
  async (explicitTimezone) => {
    vi.spyOn(intelligenceApi, "executionTimezone").mockResolvedValue(
      "America/New_York",
    );
    const preview = vi
      .spyOn(actionApi, "preview")
      .mockRejectedValue(new Error("Preview recorded"));
    const screen = await mount({
      ...configuration,
      timezone: explicitTimezone,
    });
    await screen
      .getByRole("radio", { name: "Scheduled Studio report" })
      .click();
    await expect
      .element(screen.getByLabelText("Timezone", { exact: true }))
      .toHaveValue(explicitTimezone ?? "America/New_York");
    await screen.getByLabelText("Timezone", { exact: true }).fill("UTC");
    await screen.getByRole("radio", { name: "Metric monitor" }).click();
    await screen
      .getByRole("button", { name: "Preview monitor action" })
      .click();
    await vi.waitFor(() => expect(preview).toHaveBeenCalledOnce());
    expect(preview.mock.calls[0][0]).toMatchObject({
      adapter_id: "monitor-v1",
      configuration: { timezone: explicitTimezone ?? "America/New_York" },
    });
  },
);

it("blocks preview while configured timezone is missing and recovers through retry", async () => {
  let available = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(() =>
    json(
      available ? { runtime: { execution_timezone: "UTC" } } : { runtime: {} },
    ),
  );
  const preview = vi
    .spyOn(actionApi, "preview")
    .mockRejectedValue(new Error("Preview recorded"));
  const screen = await mount();
  await expect
    .element(screen.getByRole("alert"))
    .toHaveTextContent("configured timezone is unavailable");
  await expect
    .element(screen.getByRole("button", { name: "Preview monitor action" }))
    .toBeDisabled();
  expect(preview).not.toHaveBeenCalled();
  available = true;
  const retry = screen.getByRole("button", { name: "Retry timezone" });
  retry.element().focus();
  await expect.element(retry).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  await expect
    .element(screen.getByRole("button", { name: "Preview monitor action" }))
    .toBeEnabled();
  await screen.getByRole("button", { name: "Preview monitor action" }).click();
  await vi.waitFor(() => expect(preview).toHaveBeenCalled());
  expect(preview.mock.calls[0][0].configuration.timezone).toBe("UTC");
});

it("keeps loading and timezone errors usable at narrow widths in both themes", async () => {
  await page.viewport(320, 800);
  let reject: (error: Error) => void = () => {};
  vi.spyOn(intelligenceApi, "executionTimezone").mockImplementation(
    () =>
      new Promise((_, fail) => {
        reject = fail;
      }),
  );
  const screen = await mount();
  await expect
    .element(screen.getByRole("status"))
    .toHaveTextContent("Loading configured timezone");
  await expect
    .element(screen.getByRole("button", { name: "Preview monitor action" }))
    .toBeDisabled();
  reject(new Error("Timezone permission changed"));
  await expect
    .element(screen.getByRole("alert"))
    .toHaveTextContent("Timezone permission changed");
  for (const theme of ["", "dark"]) {
    document.documentElement.classList.toggle("dark", theme === "dark");
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
    await expect
      .element(screen.getByRole("button", { name: "Retry timezone" }))
      .toBeVisible();
  }
});
