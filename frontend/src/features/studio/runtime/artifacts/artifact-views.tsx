/**
 * Chart artifacts in a Studio answer: the inline preview, the side panel that
 * edits a draft before it is saved, and the turn's save suggestion.
 *
 * Nothing here saves on its own. A chart reaches a dashboard only through the
 * dashboard dialog, after the person picks or names a dashboard and confirms.
 */
import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Info, LayoutDashboard, PanelRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { ChartInspector } from "@/features/native-dataviz/charts/chart-inspector";
import type { ChartDefinition } from "@/features/native-dataviz/contracts";
import { ApiError } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import { AddToDatavizDialog } from "../../dataviz/add-to-dataviz";
import { DatavizChart } from "../components/registry";
import type { ArtifactPart, SaveSuggestionPart } from "../turn-reducer";
import { artifactsApi } from "./api";
import { ArtifactContext, useOpenDashboard, type ArtifactScope } from "./scope";
import type { ArtifactSaved } from "./types";

export function ArtifactProvider({ value, children }: { value: ArtifactScope; children: ReactNode }) {
  return <ArtifactContext.Provider value={value}>{children}</ArtifactContext.Provider>;
}

function SavedLink({ saved }: { saved: ArtifactSaved }) {
  const open = useOpenDashboard();
  return (
    <span className="inline-flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
      <span className="truncate">Saved in {saved.dashboard_title || "a dashboard"}</span>
      <span aria-hidden="true">·</span>
      <button
        type="button"
        className="shrink-0 rounded-sm font-medium text-foreground underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        onClick={(event) => {
          event.stopPropagation();
          open(saved);
        }}
      >
        Open
      </button>
    </span>
  );
}

/**
 * A draft chart in the answer: the same native chart as a dashboard, with an
 * edit state. Clicking it opens the artifact panel.
 */
export function ArtifactPreviewCard({ part, scope }: { part: ArtifactPart; scope: ArtifactScope }) {
  const { draft } = part;
  const action = (
    <div className="flex shrink-0 items-center gap-2">
      {part.saved ? <SavedLink saved={part.saved} /> : draft.revision > 1 ? (
        <span className="text-xs text-muted-foreground">Edited</span>
      ) : null}
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-7 gap-1.5 px-2 text-xs"
        disabled={!scope.ready}
        onClick={(event) => {
          event.stopPropagation();
          scope.open(part.id);
        }}
      >
        <PanelRight aria-hidden="true" className="size-3.5" />
        Open
      </Button>
    </div>
  );
  return (
    <div
      className="min-w-0 cursor-pointer rounded-xl transition-shadow hover:ring-1 hover:ring-border"
      data-artifact={draft.draft_ref}
      onClick={() => scope.ready && scope.open(part.id)}
    >
      <DatavizChart
        props={{ title: draft.title, chart: draft.chart, draft_ref: draft.draft_ref }}
        ready={scope.ready}
        action={action}
      />
    </div>
  );
}

/**
 * The turn's suggestion to save its chart drafts. When the active role cannot
 * author, it says why instead of hiding the action.
 */
export function SaveSuggestion({ part, scope }: { part: SaveSuggestionPart; scope: ArtifactScope }) {
  const [saving, setSaving] = useState<string | null>(null);
  if (part.capability === "role_cannot_author") {
    return (
      <div role="note" className="nova-chat-item flex max-w-3xl items-start gap-2 rounded-xl border bg-muted/40 px-3 py-2.5 text-sm text-muted-foreground">
        <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        <span>{part.message}</span>
      </div>
    );
  }
  const drafts = part.draftRefs.flatMap((ref) => {
    const artifact = scope.byRef.get(ref);
    return artifact ? [artifact] : [];
  });
  if (!drafts.length) return null;
  const unsaved = drafts.filter((artifact) => !part.saved[artifact.draft.draft_ref] && !artifact.saved);
  const savingPart = drafts.find((artifact) => artifact.draft.draft_ref === saving);
  return (
    <div className="nova-chat-item flex max-w-3xl flex-col gap-2 rounded-xl border px-3 py-2.5 text-sm" data-testid="save-suggestion">
      {unsaved.length ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <LayoutDashboard aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1">{part.message}</span>
          {unsaved.map((artifact) => (
            <Button
              key={artifact.id}
              type="button"
              size="sm"
              variant="outline"
              className="max-w-full"
              disabled={!scope.ready}
              onClick={() => setSaving(artifact.draft.draft_ref)}
            >
              <span className="truncate">
                {unsaved.length === 1 ? "Save to dashboard…" : `Save “${artifact.draft.title}”…`}
              </span>
            </Button>
          ))}
        </div>
      ) : null}
      {drafts.flatMap((artifact) => {
        const saved = part.saved[artifact.draft.draft_ref] ?? artifact.saved;
        return saved ? [
          <div key={artifact.id} className="flex min-w-0 items-center gap-2">
            <LayoutDashboard aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
            {drafts.length > 1 ? <span className="truncate">{artifact.draft.title}:</span> : null}
            <SavedLink saved={saved} />
          </div>,
        ] : [];
      })}
      {savingPart ? (
        <AddToDatavizDialog
          draft={{
            draftRef: savingPart.draft.draft_ref,
            title: savingPart.draft.title,
            onSaved: (result) => {
              if (result.event) scope.apply(result.event);
            },
          }}
          onClose={() => setSaving(null)}
        />
      ) : null}
    </div>
  );
}

/** Edit a draft before saving it: the native chart inspector over the draft's query. */
export function ArtifactSidePanel({
  part,
  scope,
  onClose,
}: {
  part: ArtifactPart | null;
  scope: ArtifactScope;
  onClose: () => void;
}) {
  return (
    <Sheet open={Boolean(part)} onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-xl">
        {part ? <PanelBody key={part.id} part={part} scope={scope} /> : null}
      </SheetContent>
    </Sheet>
  );
}

function PanelBody({ part, scope }: { part: ArtifactPart; scope: ArtifactScope }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const ref = part.draft.draft_ref;
  const state = useQuery({
    queryKey: ["studio", "artifact", epoch, ref],
    queryFn: ({ signal }) => artifactsApi.state(ref, signal),
    retry: false,
    staleTime: 0,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [saveOpen, setSaveOpen] = useState(false);
  const chart = part.draft.chart;
  const datasets = useMemo(() => [], []);

  const apply = async (edited: ChartDefinition) => {
    setBusy(true);
    setError(undefined);
    try {
      const result = await artifactsApi.revise(ref, edited, part.draft.revision);
      scope.apply(result.event);
      void state.refetch();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "The change could not be kept. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const capability = state.data?.capability;
  return (
    <>
      <SheetHeader className="shrink-0 gap-1 border-b px-4 py-3 pr-12">
        <SheetTitle className="truncate">{part.draft.title}</SheetTitle>
        <SheetDescription>
          {part.saved
            ? "A copy of this chart is saved. Changes here stay in this answer until you save again."
            : "Not saved yet. Change the chart here, then save it to a dashboard when it looks right."}
        </SheetDescription>
        <div className="flex min-h-8 flex-wrap items-center gap-2 pt-1">
          {part.saved ? <SavedLink saved={part.saved} /> : null}
          {capability === "can_save" ? (
            <Button type="button" size="sm" className="ms-auto" onClick={() => setSaveOpen(true)}>
              <LayoutDashboard aria-hidden="true" />
              Save to dashboard…
            </Button>
          ) : capability === "role_cannot_author" ? (
            <p role="note" className="flex items-start gap-1.5 text-xs text-muted-foreground">
              <Info aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
              {state.data?.message}
            </p>
          ) : null}
        </div>
      </SheetHeader>
      <div className="min-h-0 flex-1">
        {state.isPending ? (
          <p role="status" className="p-4 text-sm text-muted-foreground">Loading the chart’s fields…</p>
        ) : state.isError ? (
          <p role="alert" className="p-4 text-sm text-muted-foreground">
            {state.error instanceof ApiError ? state.error.message : "This chart is not available right now."}
          </p>
        ) : !state.data.fields.length ? (
          <p role="note" className="p-4 text-sm text-muted-foreground">{state.data.message}</p>
        ) : (
          <ChartInspector
            key={part.draft.revision}
            datasets={datasets}
            chart={chart}
            saving={busy}
            error={error}
            onSave={(edited) => void apply(edited)}
            onClose={() => undefined}
            draft={{ fields: state.data.fields, draftRef: ref, saveLabel: "Apply changes" }}
          />
        )}
      </div>
      {saveOpen ? (
        <AddToDatavizDialog
          draft={{
            draftRef: ref,
            title: part.draft.title,
            onSaved: (result) => {
              if (result.event) scope.apply(result.event);
              void state.refetch();
            },
          }}
          onClose={() => setSaveOpen(false)}
        />
      ) : null}
    </>
  );
}
