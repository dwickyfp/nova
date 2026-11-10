import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { AssistantProvider } from "./assistant-provider";
import { useNevaSurface } from "./neva-surface-hook";
import { defineNevaCapability } from "./surface-registry";

function HistoryPage() {
  const [status, setStatus] = useState("");
  const { askNeva } = useNevaSurface({
    id: "monitoring.query_history", route: "/query-history", title: "Query history",
    context: () => ({ view: { filters: { status } } }),
    capabilities: [defineNevaCapability({
      name: "surface.set_filter", risk: "safe",
      argsSchema: z.object({ filter: z.literal("status"), value: z.enum(["", "ERROR", "SUCCESS"]) }),
      execute: ({ value }) => setStatus(value),
    })],
    suggestedActions: [{ label: "Show failed", prompt: "Show only failed queries." }],
  });
  return <div>
    <span data-testid="status">{status || "all"}</span>
    <button onClick={() => void askNeva("Show only failed queries.")}>filter with Neva</button>
    <button onClick={() => void askNeva("Did that filter work?")}>ask outcome</button>
  </div>;
}

describe("Neva page integration", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends active context, executes a validated UI action, and returns outcome evidence next turn", async () => {
    const posted: Array<Record<string, unknown>> = [];
    const acknowledged: Array<Record<string, unknown>> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/assistant/threads") && init?.method === "POST") {
        return new Response(JSON.stringify({ thread_id: "thread-1" }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/assistant/threads/thread-1/messages") && init?.method === "POST") {
        posted.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        const sse = posted.length === 1
          ? 'event: client_action\ndata: {"capability":"surface.set_filter","args":{"filter":"status","value":"ERROR"},"correlation_id":"action-1","surface_id":"monitoring.query_history"}\n\nevent: done\ndata: {"message_id":"answer-1","finish_reason":"stop"}\n\n'
          : 'event: done\ndata: {"message_id":"answer-2","finish_reason":"stop"}\n\n';
        return new Response(sse, { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }
      if (url.endsWith("/assistant/threads/thread-1/application-events") && init?.method === "POST") {
        acknowledged.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return new Response(JSON.stringify({ recorded: true, verification: "verified" }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(null, { status: 204 });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const screen = await render(
      <QueryClientProvider client={client}>
        <AssistantProvider loadWorkspaceDefaults={false}><HistoryPage /></AssistantProvider>
      </QueryClientProvider>,
    );
    await screen.getByRole("button", { name: "filter with Neva" }).click();
    await expect.element(screen.getByTestId("status")).toHaveTextContent("ERROR");
    // A done action shows on the page; the panel adds no status line.
    await vi.waitFor(() => expect(acknowledged).toHaveLength(1));
    expect(screen.container.textContent).not.toContain("Page action applied");
    expect(acknowledged).toEqual([expect.objectContaining({
      type: "ui_action_completed", correlationId: "action-1", status: "success",
    })]);
    expect((posted[0].app_context as { surface: { id: string }; capabilities: string[] }).surface.id).toBe("monitoring.query_history");
    expect((posted[0].app_context as { capabilities: string[] }).capabilities).toContain("surface.set_filter");

    await screen.getByRole("button", { name: "ask outcome" }).click();
    await vi.waitFor(() => expect(posted).toHaveLength(2));
    const context = posted[1].app_context as { view: { filters: { status: string } }; events: Array<{ type: string; correlationId: string }> };
    expect(context.view.filters.status).toBe("ERROR");
    expect(context.events).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "ui_action_completed", correlationId: "action-1" }),
    ]));
  });

  it("reports a failed refresh as an action failure", async () => {
    const acknowledged: Array<Record<string, unknown>> = [];
    const failed = vi.spyOn(toast, "error");
    const load = vi.fn()
      .mockResolvedValueOnce("ready")
      .mockRejectedValue(new Error("Refresh failed"));
    function RefreshPage() {
      const query = useQuery({ queryKey: ["neva-refresh-test"], queryFn: load, retry: false });
      const { askNeva } = useNevaSurface({
        id: "roles.list", route: "/roles", context: () => ({}),
        capabilities: [defineNevaCapability({
          name: "surface.refresh", risk: "safe", argsSchema: z.object({}).strict(),
          execute: () => query.refetch({ throwOnError: true }),
        })],
      });
      return <div>
        <span data-testid="query-status">{query.data ?? "loading"}</span>
        <button onClick={() => void askNeva("Refresh this view.")}>refresh with Neva</button>
      </div>;
    }
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/assistant/threads") && init?.method === "POST") {
        return new Response(JSON.stringify({ thread_id: "thread-refresh" }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/assistant/threads/thread-refresh/messages") && init?.method === "POST") {
        return new Response(
          'event: client_action\ndata: {"capability":"surface.refresh","args":{},"correlation_id":"refresh-1","surface_id":"roles.list"}\n\nevent: done\ndata: {"message_id":"answer-refresh","finish_reason":"stop"}\n\n',
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        );
      }
      if (url.endsWith("/assistant/threads/thread-refresh/application-events") && init?.method === "POST") {
        acknowledged.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return new Response(JSON.stringify({ recorded: true, verification: "failed" }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(null, { status: 204 });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const screen = await render(
      <QueryClientProvider client={client}>
        <AssistantProvider loadWorkspaceDefaults={false}><RefreshPage /></AssistantProvider>
      </QueryClientProvider>,
    );
    await expect.element(screen.getByTestId("query-status")).toHaveTextContent("ready");
    await screen.getByRole("button", { name: "refresh with Neva" }).click();
    await vi.waitFor(() => expect(failed).toHaveBeenCalledWith("Refresh failed"));
    expect(acknowledged).toEqual([expect.objectContaining({
      type: "ui_action_failed", correlationId: "refresh-1", status: "failure",
    })]);
  });
});
