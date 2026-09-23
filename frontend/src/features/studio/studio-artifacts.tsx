import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  BarChart3,
  Loader2,
  PackageOpen,
  RefreshCw,
  Search,
  Send,
  Table2,
} from "lucide-react";
import hljs from "highlight.js/lib/core";
import sql from "highlight.js/lib/languages/sql";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ChartBlock as VegaChart } from "@/features/agents/chart-block";
import {
  studioApi,
  type StudioArtifact,
  type StudioArtifactEditMessage,
  type StudioArtifactEditResponse,
  type StudioArtifactRefresh,
} from "@/features/agents/api";
import { cn } from "@/lib/utils";
import { formatArtifactSql } from "./artifact-source";
import { ArtifactFilterBar } from "./artifact-filter-bar";
import {
  filterArtifactRows,
  type ArtifactFilter,
  type ArtifactFilterMode,
} from "./artifact-filters";

hljs.registerLanguage("sql", sql);

/** Highlight formatted SQL for reading. Falls back to plain text on failure. */
function highlightArtifactSql(formatted: string): string | null {
  try {
    return hljs.highlight(formatted, { language: "sql" }).value;
  } catch {
    return null;
  }
}

export function StudioArtifacts({
  onSelectAgent,
}: {
  onSelectAgent: (id: string) => void;
}) {
  void onSelectAgent;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const artifactsQuery = useQuery({
    queryKey: ["studio", "artifacts"],
    queryFn: () => studioApi.listArtifacts(),
  });

  const artifacts = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const rows = artifactsQuery.data?.artifacts ?? [];
    if (!needle) return rows;
    return rows.filter((artifact) =>
      artifact.title.toLowerCase().includes(needle),
    );
  }, [artifactsQuery.data?.artifacts, search]);

  if (selectedId) {
    return (
      <ArtifactDetail
        artifactId={selectedId}
        onBack={() => setSelectedId(null)}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-4 border-b px-5 py-3 sm:px-7">
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold">Artifacts</h1>
          <p className="text-xs text-muted-foreground">
            Saved views rerun their SQL whenever they are opened.
          </p>
        </div>
        <div className="relative w-full sm:w-64">
          <Search
            aria-hidden="true"
            className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search artifacts"
            aria-label="Search artifacts"
            className="pl-8"
          />
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6 sm:px-7">
        {artifactsQuery.isLoading ? (
          <LoadingState label="Loading artifacts" />
        ) : artifactsQuery.isError ? (
          <ErrorState
            message={(artifactsQuery.error as Error).message}
            onRetry={() => void artifactsQuery.refetch()}
          />
        ) : !artifactsQuery.data?.count ? (
          <EmptyState
            icon={PackageOpen}
            title="No saved artifacts"
            description="Save a chart or result table from a conversation. Nova keeps its SQL and rebuilds the view from current data."
          />
        ) : !artifacts.length ? (
          <EmptyState
            icon={Search}
            title="No matching artifacts"
            description={`Nothing matches “${search.trim()}”.`}
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {artifacts.map((artifact) => (
              <ArtifactCard
                key={artifact.artifact_id}
                artifact={artifact}
                onOpen={() => setSelectedId(artifact.artifact_id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ArtifactCard({
  artifact,
  onOpen,
}: {
  artifact: StudioArtifact;
  onOpen: () => void;
}) {
  const preview = useArtifactData(artifact.artifact_id);
  return (
    <article
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      className="group min-w-0 cursor-pointer overflow-hidden rounded-xl border bg-card shadow-sm transition-[border-color,box-shadow] hover:border-foreground/20 hover:shadow-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <div className="flex items-start gap-3 border-b px-4 py-3">
        <ArtifactIcon type={artifact.artifact_type} />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-medium">{artifact.title}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Updated {formatTimestamp(artifact.updated_at)}
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label={`Refresh ${artifact.title}`}
          disabled={preview.isFetching}
          onClick={(event) => {
            event.stopPropagation();
            void preview.refetch();
          }}
        >
          <RefreshCw
            aria-hidden="true"
            className={cn("size-3.5", preview.isFetching && "animate-spin")}
          />
        </Button>
      </div>
      <div className="min-w-0 p-4">
        {preview.isLoading ? (
          <LoadingState label="Refreshing preview" compact />
        ) : preview.isError ? (
          <p className="flex items-center gap-2 py-8 text-sm text-destructive">
            <AlertCircle aria-hidden="true" className="size-4" />
            The current data could not be loaded.
          </p>
        ) : preview.data ? (
          <ArtifactVisual data={preview.data} compact />
        ) : null}
      </div>
    </article>
  );
}

function ArtifactDetail({
  artifactId,
  onBack,
}: {
  artifactId: string;
  onBack: () => void;
}) {
  const queryClient = useQueryClient();
  const detail = useArtifactData(artifactId);
  const [filters, setFilters] = useState<ArtifactFilter[]>([]);
  const [filterMode, setFilterMode] = useState<ArtifactFilterMode>("all");
  const [draftResponse, setDraftResponse] =
    useState<StudioArtifactEditResponse | null>(null);
  const [editHistory, setEditHistory] = useState<StudioArtifactEditMessage[]>(
    [],
  );
  const [chatInput, setChatInput] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [chatBusy, setChatBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [editNotice, setEditNotice] = useState<string | null>(null);
  const displayedData =
    detail.data && draftResponse?.draft
      ? {
          ...detail.data,
          artifact: { ...detail.data.artifact, ...draftResponse.draft },
          columns: draftResponse.columns,
          rows: draftResponse.rows,
          row_count: draftResponse.row_count,
          elapsed_ms: draftResponse.elapsed_ms,
        }
      : detail.data;
  const filteredRows = useMemo(
    () =>
      displayedData
        ? filterArtifactRows(
            displayedData.columns,
            displayedData.rows,
            filters,
            filterMode,
          )
        : [],
    [displayedData, filters, filterMode],
  );
  const remove = useMutation({
    mutationFn: () => studioApi.deleteArtifact(artifactId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["studio", "artifacts"],
      });
      toast.success("Artifact deleted");
      onBack();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const sendEdit = async () => {
    const instruction = chatInput.trim();
    if (!instruction || chatBusy || !detail.data) return;
    const history = editHistory.slice(-8);
    setEditHistory((messages) => [
      ...messages,
      { role: "user", content: instruction },
    ]);
    setChatInput("");
    setEditNotice(null);
    setChatBusy(true);
    try {
      const response = await studioApi.editArtifact(artifactId, {
        instruction,
        draft: draftResponse?.draft ?? null,
        columns: displayedData?.columns ?? [],
        history,
      });
      setEditHistory((messages) => [
        ...messages,
        { role: "assistant", content: response.message },
      ]);
      if (response.draft) {
        setDraftResponse(response);
      } else {
        setEditNotice(response.message);
      }
    } catch (error) {
      setEditHistory(history);
      setChatInput(instruction);
      toast.error((error as Error).message);
    } finally {
      setChatBusy(false);
    }
  };

  const applyEdit = async () => {
    if (!draftResponse?.draft || !detail.data || applyBusy) return;
    setApplyBusy(true);
    try {
      const saved = await studioApi.applyArtifactEdit(artifactId, {
        draft: draftResponse.draft,
        expected_updated_at: detail.data.artifact.updated_at,
      });
      queryClient.setQueryData(["studio", "artifact-data", artifactId], saved);
      await queryClient.invalidateQueries({
        queryKey: ["studio", "artifacts"],
      });
      setDraftResponse(null);
      setEditHistory([]);
      setEditNotice(null);
      toast.success("Artifact updated");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setApplyBusy(false);
    }
  };

  if (detail.isLoading) {
    return <LoadingState label="Opening artifact" />;
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <DetailHeader onBack={onBack} title="Artifact" />
        <ErrorState
          message={(detail.error as Error)?.message || "Artifact not found"}
          onRetry={() => void detail.refetch()}
        />
      </div>
    );
  }

  const { artifact } = detail.data;
  const activeData = displayedData ?? detail.data;
  const visibleData = { ...activeData, rows: filteredRows };
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <DetailHeader
        onBack={onBack}
        title={artifact.title}
        subtitle={`${draftResponse?.draft ? "Draft" : `Updated ${formatTimestamp(artifact.updated_at)}`} · ${activeData.row_count.toLocaleString()} rows · ${formatDuration(activeData.elapsed_ms)}`}
        refreshing={detail.isFetching}
        onRefresh={() => void detail.refetch()}
        onDelete={() => setDeleteOpen(true)}
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-7">
        {draftResponse?.draft ? (
          <div className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border bg-muted/40 p-3">
            <div className="min-w-0 flex-1 text-sm">
              <p className="font-medium">Draft preview</p>
              <p className="text-xs text-muted-foreground">
                Review the result, then apply it to the saved artifact.
              </p>
            </div>
          </div>
        ) : null}
        <ArtifactFilterBar
          columns={activeData.columns}
          rows={activeData.rows}
          filters={filters}
          onFiltersChange={setFilters}
          mode={filterMode}
          onModeChange={setFilterMode}
          visibleCount={filteredRows.length}
        />
        <section
          aria-label="Artifact preview"
          aria-busy={chatBusy}
          data-ai-processing={chatBusy ? "true" : undefined}
          className={cn(
            "min-w-0 rounded-xl border bg-card p-4 shadow-sm",
            chatBusy && "artifact-ai-active",
          )}
        >
          <span className="sr-only" role="status">
            {chatBusy ? "Nova is preparing an artifact preview." : ""}
          </span>
          {activeData.rows.length > 0 && filteredRows.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No rows match these filters.
            </p>
          ) : (
            <ArtifactVisual data={visibleData} compact />
          )}
        </section>

        <Tabs defaultValue="table" className="mt-5 min-w-0">
          <TabsList aria-label="Artifact data views">
            <TabsTrigger value="table">Table</TabsTrigger>
            <TabsTrigger value="sql">SQL</TabsTrigger>
          </TabsList>
          <TabsContent value="table" className="mt-2">
            <ArtifactTable
              columns={activeData.columns}
              rows={filteredRows}
              showRowNumbers
              emptyMessage={
                activeData.rows.length > 0
                  ? "No rows match these filters."
                  : undefined
              }
            />
          </TabsContent>
          <TabsContent value="sql" className="mt-2">
            <div className="min-w-0 overflow-x-hidden rounded-xl border bg-card">
              <pre
                data-testid="artifact-sql"
                className="hljs w-full min-w-0 whitespace-pre-wrap break-words p-4 font-mono text-sm leading-6 [overflow-wrap:anywhere]"
              >
                <code
                  className="font-mono"
                  // highlight.js emits only span elements with token class
                  // names, so the markup is inert.
                  dangerouslySetInnerHTML={{
                    __html:
                      highlightArtifactSql(
                        formatArtifactSql(activeData.artifact.sql_text),
                      ) ?? "",
                  }}
                />
              </pre>
            </div>
          </TabsContent>
        </Tabs>
      </div>

      <form
        className="shrink-0 bg-background px-4 py-3 sm:px-7"
        onSubmit={(event) => {
          event.preventDefault();
          void sendEdit();
        }}
      >
        {editNotice ? (
          <p
            className="mx-auto mb-2 max-w-3xl text-xs text-muted-foreground"
            role="status"
          >
            {editNotice}
          </p>
        ) : null}
        {draftResponse?.draft ? (
          <div className="mx-auto mb-2 flex max-w-3xl items-center justify-end gap-2">
            <span className="mr-auto text-xs text-muted-foreground">
              Draft ready to review
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setDraftResponse(null);
                setEditHistory([]);
                setEditNotice(null);
              }}
              disabled={applyBusy}
            >
              Discard draft
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => void applyEdit()}
              disabled={applyBusy}
            >
              {applyBusy ? (
                <Loader2 aria-hidden="true" className="size-4 animate-spin" />
              ) : null}
              Apply changes
            </Button>
          </div>
        ) : null}
        <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border bg-card p-2 shadow-sm focus-within:border-ring">
          <textarea
            value={chatInput}
            onChange={(event) => setChatInput(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                void sendEdit();
              }
            }}
            aria-label="Edit artifact with Nova"
            placeholder="Ask Nova to add a column or change this to a bar or line chart"
            rows={2}
            disabled={chatBusy || applyBusy}
            className="max-h-32 min-h-12 flex-1 resize-none bg-transparent px-2 py-1 text-sm outline-none placeholder:text-muted-foreground"
          />
          <Button
            type="submit"
            size="icon"
            aria-label="Send artifact edit"
            disabled={!chatInput.trim() || chatBusy || applyBusy}
          >
            {chatBusy ? (
              <Loader2 aria-hidden="true" className="size-4 animate-spin" />
            ) : (
              <Send aria-hidden="true" className="size-4" />
            )}
          </Button>
        </div>
      </form>
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete artifact?"
        desc={`“${artifact.title}” will be deleted.`}
        confirmText="Delete artifact"
        destructive
        isLoading={remove.isPending}
        handleConfirm={() => {
          setDeleteOpen(false);
          remove.mutate();
        }}
      />
    </div>
  );
}

function DetailHeader({
  onBack,
  title,
  subtitle,
  refreshing,
  onRefresh,
  onDelete,
}: {
  onBack: () => void;
  title: string;
  subtitle?: string;
  refreshing?: boolean;
  onRefresh?: () => void;
  onDelete?: () => void;
}) {
  return (
    <header className="flex shrink-0 items-center gap-3 border-b px-4 py-3 sm:px-6">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-8"
        onClick={onBack}
        aria-label="Back to artifacts"
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
      </Button>
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-sm font-semibold">{title}</h1>
        {subtitle ? (
          <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
        ) : null}
      </div>
      {onRefresh ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={refreshing}
          onClick={onRefresh}
        >
          <RefreshCw
            aria-hidden="true"
            className={cn("size-3.5", refreshing && "animate-spin")}
          />
          Refresh
        </Button>
      ) : null}
      {onDelete ? (
        <Button type="button" variant="ghost" size="sm" onClick={onDelete}>
          Delete
        </Button>
      ) : null}
    </header>
  );
}

function ArtifactVisual({
  data,
  compact = false,
}: {
  data: StudioArtifactRefresh;
  compact?: boolean;
}) {
  if (data.artifact.artifact_type === "chart" && data.artifact.chart_spec) {
    return (
      <div className={cn("min-w-0", compact && "text-xs")}>
        <VegaChart spec={chartSpecWithRows(data)} />
      </div>
    );
  }
  return (
    <ArtifactTable
      columns={data.columns}
      rows={compact ? data.rows.slice(0, 6) : data.rows}
    />
  );
}

function ArtifactTable({
  columns,
  rows,
  showRowNumbers = false,
  emptyMessage = "The query returned no rows.",
}: {
  columns: string[];
  rows: (string | number | null)[][];
  showRowNumbers?: boolean;
  emptyMessage?: string;
}) {
  return (
    <div className="min-w-0 overflow-auto rounded-lg border">
      <table className="w-full border-collapse text-sm">
        <thead className="sticky top-0 z-10 bg-muted/80 backdrop-blur">
          <tr>
            {showRowNumbers ? (
              <th className="w-px whitespace-nowrap border-b px-3 py-2 text-right font-medium text-muted-foreground">
                #
              </th>
            ) : null}
            {columns.map((column) => (
              <th
                key={column}
                className="whitespace-nowrap border-b px-3 py-2 text-left font-medium"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr
              key={rowIndex}
              className="border-b last:border-0 even:bg-muted/20"
            >
              {showRowNumbers ? (
                <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                  {rowIndex + 1}
                </td>
              ) : null}
              {columns.map((_, cellIndex) => (
                <td key={cellIndex} className="whitespace-nowrap px-3 py-2">
                  {row[cellIndex] === null ? (
                    <span className="text-muted-foreground">null</span>
                  ) : (
                    String(row[cellIndex] ?? "")
                  )}
                </td>
              ))}
            </tr>
          ))}
          {!rows.length ? (
            <tr>
              <td
                colSpan={columns.length + (showRowNumbers ? 1 : 0)}
                className="px-3 py-8 text-center text-muted-foreground"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function useArtifactData(artifactId: string) {
  return useQuery({
    queryKey: ["studio", "artifact-data", artifactId],
    queryFn: () => studioApi.refreshArtifact(artifactId),
    staleTime: 0,
    refetchOnMount: "always",
  });
}

export function chartSpecWithRows(data: StudioArtifactRefresh): string {
  const spec = structuredClone(data.artifact.chart_spec ?? {});
  const values = data.rows.map((row) =>
    Object.fromEntries(
      data.columns.map((column, index) => [column, row[index] ?? null]),
    ),
  );
  delete spec.datasets;
  delete spec.title;
  spec.data = { values };
  return JSON.stringify(spec);
}

function ArtifactIcon({ type }: { type: "chart" | "table" }) {
  const Icon = type === "chart" ? BarChart3 : Table2;
  return (
    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
      <Icon aria-hidden="true" className="size-4" />
    </span>
  );
}

function LoadingState({
  label,
  compact = false,
}: {
  label: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-center gap-2 text-sm text-muted-foreground",
        compact ? "py-8" : "min-h-0 flex-1 py-16",
      )}
    >
      <Loader2 aria-hidden="true" className="size-4 animate-spin" />
      {label}
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <AlertCircle aria-hidden="true" className="size-6 text-destructive" />
      <div>
        <p className="text-sm font-medium">Artifact data could not be loaded</p>
        <p className="mt-1 max-w-lg text-xs text-muted-foreground">{message}</p>
      </div>
      <Button type="button" variant="outline" size="sm" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatDuration(milliseconds: number): string {
  if (milliseconds < 1_000) return `${Math.round(milliseconds)} ms`;
  return `${(milliseconds / 1_000).toFixed(1)} s`;
}
