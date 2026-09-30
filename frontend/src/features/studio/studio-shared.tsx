import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, LayoutDashboard, MessageSquare, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ChartBlock } from "@/features/agents/chart-block";
import {
  sharingApi,
  type ResultRows,
  type Share,
  type SharedThread,
} from "@/features/agents/studio-intelligence-api";
import { chartSpecWithRows } from "./studio-artifacts";

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
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>{run.data.columns.map((column) => <TableHead key={column}>{column}</TableHead>)}</TableRow>
            </TableHeader>
            <TableBody>
              {run.data.rows.slice(0, 100).map((row, index) => (
                <TableRow key={index}>
                  {row.map((cell, cellIndex) => <TableCell key={cellIndex}>{String(cell ?? "")}</TableCell>)}
                </TableRow>
              ))}
            </TableBody>
          </Table>
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
  const tiles = [...(query.data?.layout.tiles ?? [])].sort((a, b) => a.y - b.y || a.x - b.x);
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
              Shared by {query.data.owner_name}. Every tile runs with your access.
            </p>
          )}
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        {query.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : query.isError ? (
          <p role="alert" className="text-sm text-destructive">This dashboard is no longer shared with you.</p>
        ) : tiles.length === 0 ? (
          <p className="text-sm text-muted-foreground">This dashboard has no tiles.</p>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {tiles.map((tile) => (
              <SharedTileCard key={tile.tile_id} dashboardId={dashboardId} artifactId={tile.artifact_id} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SharedTileCard({ dashboardId, artifactId }: { dashboardId: string; artifactId: string }) {
  const tile = useQuery({
    queryKey: ["studio", "shared", "dashboard", dashboardId, artifactId],
    queryFn: () => sharingApi.refreshTile(dashboardId, artifactId),
    retry: false,
  });
  const data = tile.data;
  return (
    <article className="flex min-h-64 min-w-0 flex-col overflow-hidden rounded-lg border bg-card">
      <h2 className="shrink-0 truncate border-b px-3 py-2 text-sm font-medium">
        {data?.artifact.title ?? "Tile"}
      </h2>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {tile.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : tile.isError || !data ? (
          <p role="alert" className="text-sm text-muted-foreground">
            {tile.error instanceof Error ? tile.error.message : "This tile could not be loaded."}
          </p>
        ) : data.artifact.artifact_type === "chart" && data.artifact.chart_spec ? (
          <ChartBlock spec={chartSpecWithRows(data)} fillHeight />
        ) : (
          <ResultTable result={data} />
        )}
      </div>
    </article>
  );
}

function ResultTable({ result }: { result: ResultRows }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>{result.columns.map((column) => <TableHead key={column}>{column}</TableHead>)}</TableRow>
        </TableHeader>
        <TableBody>
          {result.rows.slice(0, 100).map((row, index) => (
            <TableRow key={index}>
              {row.map((cell, cellIndex) => <TableCell key={cellIndex}>{String(cell ?? "")}</TableCell>)}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
