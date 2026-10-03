import { afterEach, describe, expect, it, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentVersionHistory } from "./version-history";
import type { Agent } from "../api";
import "@/styles/index.css";

const agent = {
  agent_id: "sales",
  name: "Sales",
  config_revision: "active",
  release_manifest_id: "active-manifest",
  instructions_response: "Net revenue",
} as Agent;
const version = {
  version_id: "draft",
  label: "Draft",
  configuration: {
    name: "Sales",
    instructions_response: "Explain net revenue with evidence",
  },
  created_at: "2026-10-03T01:00:00Z",
};
const manifest = {
  id: "manifest-1",
  dependencies: {
    semantic_views: [
      { view_id: "revenue", version: 3, fingerprint: "fingerprint" },
    ],
  },
};
const json = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
const setup = () =>
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
      <AgentVersionHistory agent={agent} requestedVersion="draft" />
    </QueryClientProvider>,
  );
function mock(eligible: boolean) {
  let evaluated = false;
  const run = {
    id: "run-1",
    revision: 2,
    agent_id: "sales",
    version_id: "draft",
    manifest_id: manifest.id,
    status: eligible ? "passed" : "unavailable",
    promotion_eligible: eligible,
    results: [],
    created_at: "2026-10-03T01:00:00Z",
  };
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url.includes("/versions?"))
      return json({ versions: [version], active_version_id: "active" });
    if (url.endsWith("/manifest"))
      return json(
        init?.method === "POST"
          ? manifest
          : { manifest: evaluated ? manifest : null, status: "unevaluated" },
      );
    if (url.endsWith("/quality/runs") && init?.method === "POST") {
      evaluated = true;
      return json(run);
    }
    if (url.includes("/quality/runs/")) return json(run);
    if (url.endsWith("/publish")) return json(agent);
    return json(version);
  });
}
afterEach(async () => {
  vi.restoreAllMocks();
  await page.viewport(1280, 800);
});

describe("manifested release publication", () => {
  it("blocks publication until the selected version is evaluated, then submits the exact quality run", async () => {
    const fetch = mock(true);
    const screen = await setup();
    await expect
      .element(screen.getByRole("button", { name: "Publish selected version" }))
      .toBeDisabled();
    await screen
      .getByRole("button", { name: "Evaluate selected version" })
      .click();
    await expect
      .element(
        screen.getByText(
          "Legacy run is promotion eligible; separate gate assessments were not recorded.",
        ),
      )
      .toBeVisible();
    await screen
      .getByRole("button", { name: "Publish selected version" })
      .click();
    const call = fetch.mock.calls.find(([url]) =>
      String(url).endsWith("/publish"),
    );
    expect(JSON.parse(call![1]!.body as string)).toEqual({
      expected_revision: "active",
      quality_run_id: "run-1",
    });
  });
  it("keeps unavailable runtime evidence from enabling publication", async () => {
    mock(false);
    const screen = await setup();
    await screen
      .getByRole("button", { name: "Evaluate selected version" })
      .click();
    await expect
      .element(screen.getByText("Promotion gates have not passed."))
      .toBeVisible();
    await expect
      .element(screen.getByRole("button", { name: "Publish selected version" }))
      .toBeDisabled();
  });
  it("shows a failed manifest read and prevents publication until reloaded", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
      String(input).endsWith("/manifest")
        ? json({ detail: "Dependency access changed" }, 403)
        : String(input).includes("/versions?")
          ? json({ versions: [version], active_version_id: "active" })
          : json(version),
    );
    const screen = await setup();
    await expect
      .element(screen.getByText("Release manifest could not be read."))
      .toBeVisible();
    await expect
      .element(screen.getByRole("button", { name: "Publish selected version" }))
      .toBeDisabled();
  });
});
