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
    await expect.element(view.getByRole("button", { name: /^Latest question/ })).toBeVisible();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("cursor=older"))).toBe(false);
    await view.getByRole("button", { name: "Load more conversations" }).click();
    await expect.element(view.getByRole("button", { name: /^Earlier question/ })).toBeVisible();
    await expect.element(view.getByRole("button", { name: /^Latest question/ })).toBeVisible();
    await expect.element(view.getByRole("button", { name: "Load more conversations" })).not.toBeInTheDocument();
  });

  it("searches, renames and deletes a conversation", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    const calls: { url: string; method: string; body?: string }[] = [];
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET", body: init?.body as string | undefined });
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      if (init?.method === "PATCH") return json({ thread_id: "t-1", title: "Monthly revenue",
        created_at: "2026-09-25T00:00:00Z", updated_at: "2026-09-25T00:00:00Z", message_count: 4 });
      const searching = url.includes("q=");
      return json({
        threads: [{ thread_id: "t-1", title: searching ? "Revenue analysis" : "Revenue analysis",
          created_at: "2026-09-25T00:00:00Z", updated_at: "2026-09-25T00:00:00Z", message_count: 4 }],
        count: 1,
      });
    });
    const deleted = vi.fn();
    const view = await renderHistory({
      activeThreadId: "t-1", onOpenThread: () => {}, onDeletedThread: deleted,
    });
    await view.getByRole("button", { name: "Chat history" }).click();
    await view.getByRole("textbox", { name: "Search conversations" }).fill("revenue");
    await expect.poll(() => calls.some((call) => call.url.includes("q=revenue"))).toBe(true);

    await view.getByRole("button", { name: "Actions for Revenue analysis" }).click();
    await view.getByRole("menuitem", { name: "Rename" }).click();
    await view.getByRole("textbox", { name: "Conversation name" }).fill("Monthly revenue");
    await view.getByRole("button", { name: "Save name" }).click();
    await expect.poll(() => calls.find((call) => call.method === "PATCH")?.body)
      .toBe(JSON.stringify({ title: "Monthly revenue" }));

    await view.getByRole("button", { name: "Actions for Revenue analysis" }).click();
    await view.getByRole("menuitem", { name: "Delete" }).click();
    await expect.element(view.getByRole("alertdialog")).toHaveTextContent("cannot be undone");
    await view.getByRole("button", { name: "Delete" }).click();
    await expect.poll(() => deleted.mock.calls.length).toBe(1);
    expect(deleted).toHaveBeenCalledWith("t-1");
    expect(calls.some((call) => call.method === "DELETE" && call.url.endsWith("/assistant/threads/t-1")))
      .toBe(true);
  });
});
