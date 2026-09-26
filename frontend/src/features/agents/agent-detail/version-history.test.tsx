import { afterEach, describe, expect, it, vi } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentVersionHistory } from "./version-history";
import { AgentResourceBindings } from "./resource-bindings";
import type { Agent, ResourceBindings } from "../api";
import { useState } from "react";
import "@/styles/index.css";

const agent = {
  agent_id: "sales",
  name: "Sales",
  config_revision: "active",
  instructions_response: "Use net revenue",
} as Agent;
const saved = {
  version_id: "draft",
  label: "Saved draft",
  created_at: "2026-09-26T08:00:00Z",
  configuration: { name: "Sales", instructions_response: "Use gross revenue" },
};
const json = (value: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );

function luminance(color: string) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, 1, 1);
  const channels = [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3).map((value) => {
    const s = value / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function textContrast(element: Element) {
  let background: Element | null = element;
  while (background && ["rgba(0, 0, 0, 0)", "transparent"].includes(getComputedStyle(background).backgroundColor)) background = background.parentElement;
  const a = luminance(getComputedStyle(element).color);
  const b = luminance(background ? getComputedStyle(background).backgroundColor : "white");
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
function setup(component: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    client,
    screen: render(
      <QueryClientProvider client={client}>{component}</QueryClientProvider>,
    ),
  };
}
function historyMock(publishStatus = 200) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST")
        return json(
          publishStatus === 200
            ? { ...agent, config_revision: "new" }
            : {
                detail: "Agent changed. Reload and compare before publishing.",
              },
          publishStatus,
        );
      if (url.includes("/versions?"))
        return json({
          versions: [saved],
          has_more: false,
          active_version_id: "active",
        });
      return json(saved);
    });
}
afterEach(async () => {
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
  await page.viewport(1280, 800);
});

describe("Agent version history", () => {
  it("does not publish over unsaved local edits", async () => {
    historyMock();
    const screen = await setup(<AgentVersionHistory agent={agent} requestedVersion="draft" hasUnsavedEdits />).screen;
    await expect.element(screen.getByText("Save a draft or discard your unsaved edits before publishing.")).toBeVisible();
    await expect.element(screen.getByRole("button", { name: "Publish selected version" })).toBeDisabled();
  });
  it("opens History, compares fields, and publishes with the active revision", async () => {
    const fetch = historyMock();
    const { screen: pending, client } = setup(
      <AgentVersionHistory agent={agent} />,
    );
    const screen = await pending;
    await screen.getByRole("button", { name: "History" }).click();
    await screen.getByRole("menuitem", { name: /Saved draft/ }).click();
    await expect
      .element(screen.getByRole("heading", { name: "Response instructions" }))
      .toBeVisible();
    await expect
      .element(screen.getByText("Use net revenue", { exact: true }))
      .toBeVisible();
    await expect
      .element(screen.getByText("Use gross revenue", { exact: true }))
      .toBeVisible();
    await screen
      .getByRole("button", { name: "Publish selected version" })
      .click();
    await vi.waitFor(() =>
      expect(
        client.getQueryData<Agent>(["agents", "detail", "sales"])
          ?.config_revision,
      ).toBe("new"),
    );
    const call = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(String(call?.[0])).toContain("/versions/draft/publish");
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({
      expected_revision: "active",
    });
    await expect.element(screen.getByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps comparison open after a publication conflict", async () => {
    historyMock(409);
    const screen = await setup(
      <AgentVersionHistory agent={agent} requestedVersion="draft" />,
    ).screen;
    await screen
      .getByRole("button", { name: "Publish selected version" })
      .click();
    await expect
      .element(screen.getByRole("alert"))
      .toHaveTextContent("Agent changed");
    await expect.element(screen.getByRole("dialog")).toBeVisible();
    await expect
      .element(
        screen.getByRole("button", { name: "Reload active configuration" }),
      )
      .toBeVisible();
    await screen
      .getByRole("button", { name: "Close", exact: true })
      .first()
      .click();
  });

  it("shows empty history and retries failed history reads", async () => {
    let failed = true;
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      failed
        ? json({ detail: "Unavailable" }, 503)
        : json({ versions: [], has_more: false }),
    );
    const screen = await setup(<AgentVersionHistory agent={agent} />).screen;
    await screen.getByRole("button", { name: "History" }).click();
    failed = false;
    await screen
      .getByRole("menuitem", { name: "Could not load history. Retry" })
      .click();
    await expect
      .element(screen.getByRole("menuitem", { name: "No saved versions yet" }))
      .toBeVisible();
    await screen.getByRole("menuitem", { name: "View all history" }).click();
    await expect
      .element(
        screen.getByText("Save a configuration draft to start its history."),
      )
      .toBeVisible();
    await expect
      .element(screen.getByRole("button", { name: "Publish selected version" }))
      .toBeDisabled();
  });

  it("pages history and retries a failed comparison", async () => {
    let fail = true;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/versions?")) return json({ versions: [{ ...saved, label: url.includes("offset=30") ? "Older draft" : "Latest draft" }], has_more: !url.includes("offset=30") });
      return fail ? json({ detail: "Unavailable" }, 503) : json(saved);
    });
    const screen = await setup(<AgentVersionHistory agent={agent} />).screen;
    await screen.getByRole("button", { name: "History" }).click();
    await screen.getByRole("menuitem", { name: "View all history" }).click();
    await screen.getByRole("button", { name: "Older", exact: true }).click();
    await screen.getByRole("button", { name: /Older draft/ }).click();
    await expect.element(screen.getByText("Could not load this version.")).toBeVisible();
    fail = false;
    await screen.getByRole("button", { name: "Retry comparison" }).click();
    await expect.element(screen.getByText("Use gross revenue", { exact: true })).toBeVisible();
    await screen.getByRole("button", { name: "Newer", exact: true }).click();
    await expect.element(screen.getByRole("button", { name: /Latest draft/ })).toBeVisible();
  });

  for (const width of [320, 1280])
    for (const dark of [false, true]) {
      it(`keeps long comparisons bounded at ${width}px in ${dark ? "dark" : "light"} mode`, async () => {
        await page.viewport(width, 800);
        document.documentElement.classList.toggle("dark", dark);
        vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
          String(input).includes("/versions?")
            ? json({ versions: [saved], has_more: false })
            : json({
                ...saved,
                configuration: {
                  ...saved.configuration,
                  instructions_response: "Long instructions ".repeat(1200),
                },
              }),
        );
        const screen = await setup(
          <AgentVersionHistory agent={agent} requestedVersion="draft" />,
        ).screen;
        await expect.element(screen.getByText("1 changed field")).toBeVisible();
        const dialog = screen.getByRole("dialog").element();
        expect(dialog.getBoundingClientRect().right).toBeLessThanOrEqual(width);
        expect(dialog.getBoundingClientRect().bottom).toBeLessThanOrEqual(800);
        expect(dialog.scrollWidth).toBeLessThanOrEqual(dialog.clientWidth + 1);
        for (const text of dialog.querySelectorAll("p, pre, h2, h3")) {
          expect(textContrast(text), text.textContent?.slice(0, 50)).toBeGreaterThanOrEqual(4.5);
        }
        await expect
          .element(
            screen.getByRole("button", { name: "Publish selected version" }),
          )
          .toBeVisible();
        await page.screenshot();
        await userEvent.keyboard("{Escape}");
        await expect
          .element(screen.getByRole("dialog"))
          .not.toBeInTheDocument();
      });
    }
});

describe("Agent resource bindings", () => {
  it("selects a resource and a typed fixed filter without granting other indexes", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
      String(input).includes("/ai/search")
        ? json([
            {
              name: "sales_docs",
              active_version: 2,
              filter_columns: ["region"],
            },
            { name: "finance", active_version: 1, filter_columns: [] },
          ])
        : json([{ name: "customers", active_version: 1 }]),
    );
    let current: ResourceBindings;
    function Form() {
      const [value, setValue] = useState<ResourceBindings>({
        search_indexes: [],
        feature_groups: [],
      });
      current = value;
      return <AgentResourceBindings value={value} onChange={setValue} />;
    }
    const screen = await setup(<Form />).screen;
    await screen.getByRole("checkbox", { name: "sales_docs" }).click();
    await screen.getByRole("checkbox", { name: "Fix region" }).click();
    await screen
      .getByRole("textbox", { name: "sales_docs region value" })
      .fill("ID");
    await screen.getByRole("checkbox", { name: "customers" }).click();
    expect(current!).toEqual({
      search_indexes: [{ index: "sales_docs", filters: { region: "ID" } }],
      feature_groups: ["customers"],
    });
    await screen.getByRole("combobox", { name: "sales_docs region type" }).click();
    await screen.getByRole("option", { name: "Number", exact: true }).click();
    await screen.getByRole("spinbutton", { name: "sales_docs region value" }).fill("42");
    expect(current!.search_indexes[0].filters.region).toBe(42);
    await screen.getByRole("combobox", { name: "sales_docs region type" }).click();
    await screen.getByRole("option", { name: "Boolean", exact: true }).click();
    await screen.getByRole("combobox", { name: "sales_docs region value" }).click();
    await screen.getByRole("option", { name: "True", exact: true }).click();
    expect(current!.search_indexes[0].filters.region).toBe(true);
    await screen.getByRole("checkbox", { name: "sales_docs" }).click();
    expect(current!.search_indexes).toEqual([]);
  });

  it("retries unavailable resource lists and shows empty results", async () => {
    let fail = true;
    vi.spyOn(globalThis, "fetch").mockImplementation(() => fail ? json({ detail: "Unavailable" }, 503) : json([]));
    const screen = await setup(<AgentResourceBindings value={{ search_indexes: [], feature_groups: [] }} onChange={() => {}} />).screen;
    await expect.element(screen.getByRole("button", { name: "Retry search indexes" })).toBeVisible();
    fail = false;
    await screen.getByRole("button", { name: "Retry search indexes" }).click();
    await screen.getByRole("button", { name: "Retry Feature Groups" }).click();
    await expect.element(screen.getByText("No published search indexes are available.")).toBeVisible();
    await expect.element(screen.getByText("No published Feature Groups are available.")).toBeVisible();
  });
});
