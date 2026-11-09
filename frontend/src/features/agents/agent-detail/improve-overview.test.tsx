import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/index.css";
import { ImproveOverview } from "./improve-overview";

function json(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function mock({ saved, cases }: { saved: boolean; cases: number }) {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.endsWith("/readiness"))
      return json({
        ready: false,
        score: 0.5,
        checks: [
          { id: "semantic_view", status: "ok", title: "", detail: "", fix: "" },
          { id: "synonyms:sales", status: "warn", title: "", detail: "", fix: "" },
        ],
      });
    if (url.endsWith("/quality/cases"))
      return json({
        items: Array.from({ length: cases }, (_, index) => ({
          id: `case-${index}`,
          mandatory: true,
          critical: true,
          assertions: [],
        })),
      });
    if (url.includes("/versions?"))
      return json({
        versions: saved ? [{ version_id: "v1", label: "Draft 1" }] : [],
        active_version_id: "v1",
      });
    if (url.endsWith("/quality/runs")) return json({ items: [] });
    if (url.includes("/verified-query-candidates"))
      return json({ candidates: [], count: 2 });
    if (url.endsWith("/quality/proposals"))
      return json({ items: [{ id: "p1", status: "proposed" }] });
    if (url.endsWith("/improvement-suggestions"))
      return json({ feedback: [{ count: 3 }], materialized_views: [] });
    return json({});
  });
}

function mount(onOpen = vi.fn(), onOpenConfiguration = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ImproveOverview
        agentId="sales"
        onOpen={onOpen}
        onOpenConfiguration={onOpenConfiguration}
      />
    </QueryClientProvider>,
  );
}

afterEach(async () => {
  await cleanup();
  vi.restoreAllMocks();
});

describe("Improve overview", () => {
  it("explains the four steps with where the agent stands and marks the next one", async () => {
    mock({ saved: true, cases: 0 });
    const onOpen = vi.fn();
    const screen = await mount(onOpen);

    await expect.element(screen.getByText("1 of 2 checks pass · 1 to tidy up")).toBeVisible();
    await expect.element(screen.getByText("No test questions yet")).toBeVisible();
    await expect.element(screen.getByText("Not tested yet")).toBeVisible();
    await expect
      .element(screen.getByText("2 liked answers to approve · 1 suggested fix · 3 disliked answers"))
      .toBeVisible();
    // The first unfinished step is the next one.
    const next = screen.getByRole("listitem").filter({ hasText: "Next step" });
    await expect.element(next).toHaveTextContent("Check the data it reads");

    await screen.getByRole("button", { name: "Add a test question" }).click();
    expect(onOpen).toHaveBeenCalledWith("quality");
  });

  it("sends an agent with no saved version to its configuration first", async () => {
    mock({ saved: false, cases: 2 });
    const onOpenConfiguration = vi.fn();
    const screen = await mount(vi.fn(), onOpenConfiguration);
    await expect
      .element(screen.getByText("Save the configuration first, then test it"))
      .toBeVisible();
    await screen.getByRole("button", { name: "Open Configuration" }).click();
    expect(onOpenConfiguration).toHaveBeenCalledOnce();
  });
});
