import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThreadHistory } from "./thread-history";

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

// Installed for the whole file so a late refetch from a mounted panel cannot
// reach the real API and navigate the test iframe to /sign-in.
beforeAll(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/assistant/threads")) {
      return json({
        threads: [
          {
            thread_id: "t-1",
            title: "Revenue analysis",
            workspace_file_id: null,
            created_at: "2026-01-01T10:00:00Z",
            updated_at: "2026-01-02T10:00:00Z",
            message_count: 4,
          },
        ],
        count: 1,
      });
    }
    return json({});
  });
});

afterAll(() => {
  vi.restoreAllMocks();
});

function renderHistory(props: Parameters<typeof ThreadHistory>[0]) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ThreadHistory {...props} />
    </QueryClientProvider>,
  );
}

describe("ThreadHistory", () => {
  it("does not fetch until the popover is opened", async () => {
    const { getByRole } = await renderHistory({
      activeThreadId: null,
      onOpenThread: () => {},
    });
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;

    await expect
      .element(getByRole("button", { name: "Chat history" }))
      .toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/assistant/threads"),
      ),
    ).toBe(false);
  });

  it("loads another page only on request and keeps both conversations", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockImplementation(async (input) => {
      const older = String(input).includes("cursor=older");
      return json({
        threads: [{ thread_id: older ? "old" : "new", title: older ? "Earlier question" : "Latest question",
          created_at: "2026-09-25T00:00:00Z", updated_at: "2026-09-25T00:00:00Z", message_count: 2 }],
        count: 1, next_cursor: older ? null : "older",
      });
    });
    const view = await renderHistory({ activeThreadId: null, onOpenThread: () => {} });
    await view.getByRole("button", { name: "Chat history" }).click();
    await expect.element(view.getByRole("button", { name: /Latest question/ })).toBeVisible();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("cursor=older"))).toBe(false);
    await view.getByRole("button", { name: "Load more conversations" }).click();
    await expect.element(view.getByRole("button", { name: /Earlier question/ })).toBeVisible();
    await expect.element(view.getByRole("button", { name: /Latest question/ })).toBeVisible();
    await expect.element(view.getByRole("button", { name: "Load more conversations" })).not.toBeInTheDocument();
  });
});
