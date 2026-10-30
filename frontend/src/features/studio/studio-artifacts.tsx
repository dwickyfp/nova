import { DashboardCanvas } from "@/features/viz/dashboard-canvas";
import { ArtifactExplorer } from "./artifact-explorer";
import { BiFrame } from "@/features/bi/bi-frame";
import { describeView } from "@/features/viz/insight";
import { ViewAnswer } from "@/features/viz/view-answer";
import { VizView } from "@/features/viz/viz-view";
import type { DashboardSpec, PresModel } from "@/features/viz/types";
import { chartSpecWithRows } from "./artifact-chart-spec";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  Download,
  Loader2,
  RefreshCw,
  Send,
} from "lucide-react";
import hljs from "highlight.js/lib/core";
import sql from "highlight.js/lib/languages/sql";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  studioApi,
  type StudioArtifactEditMessage,
  type StudioArtifactEditResponse,
  type StudioArtifactRefresh,
} from "@/features/agents/api";
import { downloadArtifact, type ExportFormat } from "@/features/agents/studio-intelligence-api";
import { cn } from "@/lib/utils";
import { formatArtifactSql } from "./artifact-source";
import { ArtifactFilterBar } from "./artifact-filter-bar";
import { LegacyBanner } from "./legacy-conversion";
import { UiChart } from "./intelligent-ui/ui-chart";
import { ResultTable } from "./result-table";
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
  const [selected, setSelected] = useState<{ id: string; edit: boolean } | null>(null);

  if (selected) {
    return (
      <ArtifactDetail
        key={selected.id}
        artifactId={selected.id}
        startEditing={selected.edit}
        onBack={() => setSelected(null)}
      />
    );
  }
  return <ArtifactExplorer onOpen={(id, options) => setSelected({ id, edit: Boolean(options?.edit) })} />;
}

function ArtifactDetail({
  artifactId,
  startEditing = false,
  onBack,
}: {
  artifactId: string;
  startEditing?: boolean;
  onBack: () => void;
}) {
  const queryClient = useQueryClient();
  const meta = useQuery({
    queryKey: ["studio", "artifact-meta", artifactId],
    queryFn: () => studioApi.getArtifact(artifactId),
    staleTime: 30_000,
  });
  const isDashboard = meta.data?.artifact_type === "dashboard";
  const detail = useArtifactData(artifactId, meta.isSuccess && !isDashboard);
  const [filters, setFilters] = useState<ArtifactFilter[]>([]);
  const [filterMode, setFilterMode] = useState<ArtifactFilterMode>("all");
  const [draftResponse, setDraftResponse] =
    useState<StudioArtifactEditResponse | null>(null);
  const [editHistory, setEditHistory] = useState<StudioArtifactEditMessage[]>(
    [],
  );
  const [chatInput, setChatInput] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [editing, setEditing] = useState(startEditing);
  const charts = useQuery({
    queryKey: ["studio", "artifacts"],
    queryFn: () => studioApi.listArtifacts(),
    enabled: isDashboard && Boolean(meta.data?.legacy),
  });
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

  if (meta.isLoading) {
    return <LoadingState label="Opening artifact" />;
  }
  if (meta.isError || !meta.data) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <DetailHeader onBack={onBack} title="Artifact" />
        <ErrorState
          message={(meta.error as Error)?.message || "Artifact not found"}
          onRetry={() => void meta.refetch()}
        />
      </div>
    );
  }

  const artifact = meta.data;

  if (artifact.artifact_type === "dashboard" && artifact.bi_view_path) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <DetailHeader
          onBack={onBack}
          title={artifact.title}
          subtitle={`BI dashboard · Updated ${formatTimestamp(artifact.updated_at)}`}
          onDelete={() => setDeleteOpen(true)}
          extraAction={
            <Button type="button" variant="outline" size="sm" onClick={() => setEditing((value) => !value)}>
              {editing ? "Done editing" : "Edit in BI"}
            </Button>
          }
        />
        <div className="flex min-h-0 flex-1 flex-col border-t">
          <BiFrame
            key={editing ? "edit" : "view"}
            path={(editing ? artifact.bi_edit_path : artifact.bi_view_path) ?? ""}
            title={`BI dashboard ${artifact.title}`}
          />
        </div>
        <ConfirmDialog
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          title="Delete dashboard?"
          desc={`“${artifact.title}” will be deleted from Artifacts and BI. Its charts stay.`}
          confirmText="Delete dashboard"
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

  if (artifact.artifact_type === "dashboard") {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <DetailHeader
          onBack={onBack}
          title={artifact.title}
          subtitle={`Dashboard · Updated ${formatTimestamp(artifact.updated_at)}`}
          onDelete={() => setDeleteOpen(true)}
        />
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-7">
          <LegacyBanner
            artifactId={artifact.artifact_id}
            blockers={artifact.legacy_blockers ?? []}
            notes={artifact.legacy_notes ?? []}
          />
          <DashboardCanvas
            spec={(artifact.viz_spec ?? { version: 1, zones: [] }) as unknown as DashboardSpec}
            charts={charts.data?.artifacts.filter((item) => item.artifact_type === "chart") ?? []}
          />
        </div>
        <ConfirmDialog
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          title="Delete dashboard?"
          desc={`“${artifact.title}” will be deleted. Its charts stay in Artifacts.`}
          confirmText="Delete dashboard"
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

  const activeData = displayedData ?? detail.data;
  const visibleData = { ...activeData, rows: filteredRows };
  // The refresh carries the artifact as saved now; the metadata query can lag
  // behind an editor save.
  const saved = detail.data.artifact;
  // A BI chart is drawn and edited by Superset. A chart saved before BI is
  // read-only until it is converted.
  const legacy = Boolean(artifact.legacy);
  const biView = saved.bi_view_path ?? artifact.bi_view_path ?? null;
  const biEdit = saved.bi_edit_path ?? artifact.bi_edit_path ?? null;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <DetailHeader
        onBack={onBack}
        title={artifact.title}
        subtitle={`${draftResponse?.draft ? "Draft" : `Updated ${formatTimestamp(saved.updated_at)}`} · ${activeData.row_count.toLocaleString()} rows · ${formatDuration(activeData.elapsed_ms)}`}
        refreshing={detail.isFetching}
        onRefresh={() => void detail.refetch()}
        onExport={draftResponse?.draft ? undefined : (format) => downloadArtifact(artifact.artifact_id, format)}
        onDelete={() => setDeleteOpen(true)}
        extraAction={
          biEdit && editing ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setEditing(false);
                void detail.refetch();
              }}
            >
              Done editing
            </Button>
          ) : biEdit && !editing && !draftResponse?.draft ? (
            <Button type="button" variant="outline" size="sm" onClick={() => setEditing(true)}>
              Edit in BI
            </Button>
          ) : null
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-7">
        {legacy ? (
          <LegacyBanner
            artifactId={artifact.artifact_id}
            blockers={artifact.legacy_blockers ?? []}
            notes={artifact.legacy_notes ?? []}
          />
        ) : null}
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
        {!editing ? (
          <ArtifactFilterBar
            columns={activeData.columns}
            rows={activeData.rows}
            filters={filters}
            onFiltersChange={setFilters}
            mode={filterMode}
            onModeChange={setFilterMode}
            visibleCount={filteredRows.length}
          />
        ) : null}
        {editing && biEdit ? (
          <section
            aria-label="Edit in BI"
            className="flex h-full min-h-96 min-w-0 flex-col overflow-hidden rounded-xl border bg-card shadow-sm"
          >
            <BiFrame path={biEdit} title={`Edit ${artifact.title} in BI`} />
          </section>
        ) : (
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
            {biView && !draftResponse?.draft ? (
              <div className="flex aspect-video min-h-64 min-w-0 flex-col overflow-hidden rounded-lg">
                <BiFrame
                  key={saved.updated_at}
                  path={biView}
                  title={`BI chart ${artifact.title}`}
                />
              </div>
            ) : activeData.rows.length > 0 && filteredRows.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No rows match these filters.
              </p>
            ) : (
              <ArtifactVisual data={visibleData} compact />
            )}
          </section>
        )}

        {!editing ? (
          <Tabs defaultValue="table" className="mt-5 min-w-0">
            <TabsList aria-label="Artifact data views">
              <TabsTrigger value="table">Table</TabsTrigger>
              <TabsTrigger value="sql">SQL</TabsTrigger>
            </TabsList>
            <TabsContent value="table" className="mt-2">
              <ArtifactTable
                title={artifact.title}
                columns={activeData.columns}
                rows={filteredRows}
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
        ) : null}
      </div>

      {legacy ? null : (
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
      )}
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
  onExport,
  onDelete,
  extraAction,
}: {
  onBack: () => void;
  title: string;
  subtitle?: string;
  refreshing?: boolean;
  onRefresh?: () => void;
  onExport?: (format: ExportFormat) => Promise<void>;
  onDelete?: () => void;
  extraAction?: React.ReactNode;
}) {
  const [exporting, setExporting] = useState(false);
  const exportAs = async (format: ExportFormat) => {
    if (!onExport) return;
    setExporting(true);
    try {
      await onExport(format);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The export failed.");
    } finally {
      setExporting(false);
    }
  };
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
      {extraAction}
      {onRefresh ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={refreshing}
          onClick={onRefresh}
          aria-label="Refresh"
        >
          <RefreshCw
            aria-hidden="true"
            className={cn("size-3.5", refreshing && "animate-spin")}
          />
          <span className="hidden sm:inline">Refresh</span>
        </Button>
      ) : null}
      {onExport ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button type="button" variant="outline" size="sm" disabled={exporting} aria-label="Export">
              {exporting ? (
                <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
              ) : (
                <Download aria-hidden="true" className="size-3.5" />
              )}
              <span className="hidden sm:inline">Export</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
              Re-runs with your access
            </DropdownMenuLabel>
            <DropdownMenuItem onSelect={() => void exportAs("csv")}>CSV</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void exportAs("xlsx")}>Excel (.xlsx)</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void exportAs("pdf")}>PDF</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void exportAs("pptx")}>
              PowerPoint (.pptx)
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
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
  if (data.presmodel) {
    return <SheetPreview presmodel={data.presmodel as PresModel} compact={compact} />;
  }
  if (data.artifact.artifact_type === "chart" && data.artifact.chart_spec) {
    return (
      <div className={cn("min-w-0", compact && "text-xs")}>
        <UiChart spec={chartSpecWithRows(data)} />
      </div>
    );
  }
  return (
    <ArtifactTable
      title={data.artifact.title}
      columns={data.columns}
      rows={compact ? data.rows.slice(0, 6) : data.rows}
    />
  );
}

/** A saved sheet: its answer as the headline, then the chart; selecting a
 * mark reads it against the others. */
function SheetPreview({ presmodel, compact }: { presmodel: PresModel; compact: boolean }) {
  const [selected, setSelected] = useState<string | number | null>(null);
  const insight = describeView(presmodel, selected);
  return (
    <div className="viz-numerals flex min-w-0 flex-col gap-5">
      <ViewAnswer question="" insight={insight} />
      <VizView
        presmodel={presmodel}
        compact={compact}
        highlight={insight?.highlight ?? null}
        onMarkClick={(values) => {
          const dimension = insight?.highlight?.dimension;
          if (dimension && dimension in values) setSelected(values[dimension]);
        }}
      />
    </div>
  );
}

/** Artifact rows in the Studio result grid; the page names and filters them. */
function ArtifactTable({
  title,
  columns,
  rows,
  emptyMessage = "The query returned no rows.",
}: {
  title: string;
  columns: string[];
  rows: (string | number | null)[][];
  emptyMessage?: string;
}) {
  return (
    <ResultTable
      block={{ title, columns, rows }}
      heading={false}
      filterable={false}
      note={null}
      emptyText={emptyMessage}
    />
  );
}

function useArtifactData(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: ["studio", "artifact-data", artifactId],
    queryFn: () => studioApi.refreshArtifact(artifactId),
    enabled,
    staleTime: 0,
    refetchOnMount: "always",
  });
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
