import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { listModelOptions } from "./model-client";
import { ModelSelector } from "./model-selector";

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Answers only the two AI endpoints the selector touches. The spy stays
 * installed for the whole file (restored in `afterEach` only after unmount), so
 * a late refetch cannot escape to the real API and trip the session redirect.
 */
function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/ai/providers/p1/models")) {
      return json({
        models: [
          {
            id: "m1",
            provider_id: "p1",
            name: "deepseek-v4",
            display_name: "DeepSeek V4",
            type: "llm",
            is_active: true,
          },
          {
            id: "m2",
            provider_id: "p1",
            name: "embed-x",
            display_name: null,
            type: "embedding",
            is_active: true,
          },
        ],
        count: 2,
      });
    }
    return json({
      providers: [
        {
          id: "p1",
          name: "Kenari",
          type: "openai",
          endpoint: "https://k/v1",
          is_active: true,
          has_api_key: true,
        },
        {
          id: "p2",
          name: "Idle",
          type: "openai",
          endpoint: "https://i/v1",
          is_active: false,
          has_api_key: true,
        },
        {
          id: "p3",
          name: "NoKey",
          type: "openai",
          endpoint: "https://n/v1",
          is_active: true,
          has_api_key: false,
        },
      ],
      count: 3,
    });
  });
}

// The fetch spy is installed for the whole file: a component left mounted by
// one test must not fall through to the real API on a late refetch, which would
// return 401 and navigate the test iframe to /sign-in.
beforeAll(() => {
  mockApi();
});

afterAll(() => {
  vi.restoreAllMocks();
});

function renderSelector(props: Parameters<typeof ModelSelector>[0]) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ModelSelector {...props} />
    </QueryClientProvider>,
  );
}

describe("listModelOptions", () => {
  it("keeps only active LLM models of providers that have an active key", async () => {
    const options = await listModelOptions();

    expect(options).toEqual([
      {
        model: "deepseek-v4",
        providerId: "p1",
        providerName: "Kenari",
        label: "DeepSeek V4",
      },
    ]);
  });
});

describe("ModelSelector", () => {
  it("shows the default label when nothing is selected", async () => {
    const { getByRole } = await renderSelector({
      selected: null,
      onSelect: () => {},
    });
    await expect
      .element(getByRole("combobox", { name: "Model" }))
      .toHaveTextContent("Default model");
  });

  it("shows the selected model label once a choice is made", async () => {
    const { getByRole } = await renderSelector({
      selected: { providerId: "p1", model: "deepseek-v4" },
      onSelect: () => {},
    });
    await expect
      .element(getByRole("combobox", { name: "Model" }))
      .toHaveTextContent("DeepSeek V4");
  });

  it("drops a stale selection that is no longer offered", async () => {
    const onSelect = vi.fn();
    await renderSelector({
      selected: { providerId: "p1", model: "removed-model" },
      onSelect,
    });

    await vi.waitFor(() => expect(onSelect).toHaveBeenCalledWith(null));
  });
});
