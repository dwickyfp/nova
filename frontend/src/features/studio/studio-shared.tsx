import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, LayoutDashboard, MessageSquare, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  sharingApi,
  type ResultRows,
  type Share,
  type SharedThread,
} from "@/features/agents/studio-intelligence-api";
import { VizView } from "@/features/viz/viz-view";
import type { DashboardSpec } from "@/features/viz/types";
import { ResultTable } from "./result-table";

/** Conversations and dashboards others shared with you, run with your own access. */
export function StudioShared() {
  const [opened, setOpened] = useState<Pick<Share, "object_type" | "object_id"> | null>(null);
  const query = useQuery({ queryKey: ["studio", "shared"], queryFn: sharingApi.sharedWithMe });
  const threads = query.data?.shared ?? [];
  if (opened?.object_type === "thread") {
    return <SharedThreadView threadId={opened.object_id} onBack={() => setOpened(null)} />;
  }
  if (opened?.object_type === "dashboard") {
    return <SharedDashboardView dashboardId={opened.object_id} onBack={() => setOpened(null)} />;
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b px-4 py-4 sm:px-6">
        <h1 className="text-xl font-normal">Shared with me</h1>
        <p className="text-sm text-muted-foreground">
          Conversations and dashboards teammates shared. Results run with your own access.
        </p>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        {query.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : query.isError ? (
          <p role="alert" className="text-sm text-destructive">Shared items could not be loaded.</p>
        ) : threads.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing has been shared with you yet.</p>
        ) : (
          <ul className="mx-auto max-w-3xl divide-y rounded-lg border">
            {threads.map((item) => (
              <li key={item.share_id}>
                <button
                  type="button"
                  className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  onClick={() => setOpened(item)}
                >
                  {item.object_type === "dashboard" ? (
                    <LayoutDashboard aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  ) : (
                    <MessageSquare aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  )}
                  <span className="flex min-w-0 flex-col gap-0.5">
                    <span className="text-sm">
                      {item.object_type === "dashboard" ? "Dashboard" : "Conversation"} from {item.owner_name}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      Shared with {item.target_type === "role" ? `role ${item.target_name}` : "you"}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function SharedThreadView({ threadId, onBack }: { threadId: string; onBack: () => void }) {
  const query = useQuery({
    queryKey: ["studio", "shared", "thread", threadId],
    queryFn: () => sharingApi.openThread(threadId),
  });
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b px-4 py-3 sm:px-6">
        <Button variant="ghost" size="icon" onClick={onBack} aria-label="Back to shared items">
          <ArrowLeft className="size-4" />
        </Button>
        <div className="min-w-0">
          <h1 className="truncate text-base font-normal">{query.data?.title ?? "Shared conversation"}</h1>
          {query.data && (
            <p className="text-xs text-muted-foreground">Shared by {query.data.owner_name}</p>
          )}
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-6 sm:px-6">
          {query.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : query.isError ? (
            <p role="alert" className="text-sm text-destructive">This conversation is no longer shared with you.</p>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">{query.data?.note}</p>
              {query.data?.messages.map((message) => (
                <SharedMessage key={message.message_id} threadId={threadId} message={message} />
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function SharedMessage({
  threadId, message,
}: {
  threadId: string;
  message: SharedThread["messages"][number];
}) {
  const results = message.steps.filter(
    (step) => step.kind === "tool" && step.name === "semantic_query" && step.tool_call_id,
  );
  if (message.role === "user") {
    return <p className="self-end rounded-2xl bg-muted px-4 py-2 text-sm break-words">{message.content}</p>;
  }
  return (
    <div className="space-y-3">
      <p className="text-sm whitespace-pre-wrap break-words">{message.content}</p>
      {results.map((step) => (
        <SharedResult key={step.tool_call_id} threadId={threadId} toolCallId={step.tool_call_id as string}
          question={step.trace_detail?.question ?? "Result"} />
      ))}
    </div>
  );
}

function SharedResult({
  threadId, toolCallId, question,
}: {
  threadId: string;
  toolCallId: string;
  question: string;
}) {
  const run = useMutation<ResultRows, Error>({
    mutationFn: () => sharingApi.refreshResult(threadId, toolCallId),
  });
  return (
    <div className="rounded-lg border">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
        <span className="min-w-0 text-sm text-muted-foreground break-words">{question}</span>
        <Button size="sm" variant="outline" disabled={run.isPending} onClick={() => run.mutate()}>
          <Play className="size-3.5" />
          Run with my access
        </Button>
      </div>
      {run.isError ? (
        <p role="alert" className="px-3 py-2 text-sm text-destructive">{run.error.message}</p>
      ) : run.data ? (
        <div className="p-2">
          <ResultTable
            block={{ title: question, columns: run.data.columns, rows: run.data.rows }}
            heading={false}
            note={null}
          />
        </div>
      ) : null}
    </div>
  );
}

function SharedDashboardView({ dashboardId, onBack }: { dashboardId: string; onBack: () => void }) {
  const query = useQuery({
    queryKey: ["studio", "shared", "dashboard", dashboardId],
    queryFn: () => sharingApi.openDashboard(dashboardId),
  });
  const spec = query.data?.viz_spec as unknown as DashboardSpec | null;
  const compute = useQuery({
    queryKey: ["studio", "shared", "dashboard", dashboardId, "compute"],
    enabled: Boolean(spec),
    retry: false,
    queryFn: () =>
      sharingApi.computeDashboard(dashboardId, {
        spec: spec as unknown as Record<string, unknown>,
      }),
  });
  const zones = spec?.zones.filter((zone) => zone.kind === "sheet") ?? [];
  const rows = Math.max(1, ...(spec?.zones ?? []).map((zone) => zone.rect.y + zone.rect.h));
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b px-4 py-3 sm:px-6">
        <Button variant="ghost" size="icon" onClick={onBack} aria-label="Back to shared items">
          <ArrowLeft className="size-4" />
        </Button>
        <div className="min-w-0">
          <h1 className="truncate text-base font-normal">{query.data?.title ?? "Shared dashboard"}</h1>
          {query.data && (
            <p className="text-xs text-muted-foreground">
              Shared by {query.data.owner_name}. Every chart runs with your access.
            </p>
          )}
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        {query.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : query.isError ? (
          <p role="alert" className="text-sm text-destructive">This dashboard is no longer shared with you.</p>
        ) : zones.length === 0 ? (
          <p className="text-sm text-muted-foreground">This dashboard has no charts.</p>
        ) : (
          <div
            className="grid gap-4"
            style={{
              gridTemplateColumns: "repeat(24, minmax(0, 1fr))",
              gridTemplateRows: `repeat(${rows}, minmax(8rem, auto))`,
            }}
          >
            {zones.map((zone) => {
              const result = compute.data?.zones.find((item) => item.zone_id === zone.zone_id);
              return (
                <article
                  key={zone.zone_id}
                  className="flex min-w-0 flex-col overflow-hidden rounded-lg border bg-card p-3"
                  style={{
                    gridColumn: `${zone.rect.x + 1} / span ${zone.rect.w}`,
                    gridRow: `${zone.rect.y + 1} / span ${zone.rect.h}`,
                  }}
                >
                  <h2 className="mb-1 shrink-0 truncate text-xs font-medium text-muted-foreground">
                    {result?.title ?? "Chart"}
                  </h2>
                  <div className="min-h-0 flex-1">
                    {compute.isLoading ? (
                      <Skeleton className="h-32 w-full" />
                    ) : result?.error ? (
                      <p role="alert" className="text-sm text-muted-foreground">{result.error}</p>
                    ) : result?.presmodel ? (
                      <VizView presmodel={result.presmodel} compact />
                    ) : (
                      <p className="text-sm text-muted-foreground">No data.</p>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
