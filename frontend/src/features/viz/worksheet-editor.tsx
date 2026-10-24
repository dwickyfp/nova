import { type DragEvent, type ReactNode, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  ArrowDownUp,
  ChevronDown,
  Code2,
  Copy,
  Equal,
  Filter as FilterIcon,
  Loader2,
  Plus,
  Redo2,
  Search,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { studioApi, type VizSourceBody } from "@/features/agents/api";
import { cn } from "@/lib/utils";
import { CalcEditor } from "./calc-editor";
import { FilterDialog } from "./filter-dialog";
import type {
  Aggregation,
  CalculatedField,
  DataPaneField,
  FieldRef,
  Filter,
  MarkType,
  PresModel,
  ShowMeSuggestion,
  WorksheetSpec,
} from "./types";
import { describeView, type ViewInsight } from "./insight";
import { VizView } from "./viz-view";
import {
  AGGREGATION_LABELS,
  applyShowMe,
  DATE_PARTS,
  ENCODING_LABELS,
  type Encoding,
  NUMERIC_AGGREGATIONS,
  type PillLocation,
  QUICK_TABLE_CALCS,
  RECORDS_FIELD,
  type Shelf,
  asDimension,
  calculationDependents,
  clearSheet,
  filterCaption,
  filterSummary,
  hasView,
  historyOf,
  historyReducer,
  isContinuous,
  isMeasure,
  isTemporal,
  movePill,
  pillAt,
  pillCaption,
  pillFromField,
  pillParts,
  placePill,
  quickTableCalc,
  recordsPill,
  removeCalculation,
  removeFilter,
  removePill,
  setAggregation,
  setDatePart,
  setQuickTableCalc,
  setSort,
  sortDirection,
  swapShelves,
  toggleDiscrete,
  updatePill,
  upsertCalculation,
  upsertFilter,
  viewDimensions,
} from "./worksheet-model";

/**
 * WorksheetEditor is the Studio authoring surface for one chart: a data pane
 * of dimensions, measures and calculated fields; Columns and Rows shelves;
 * Filters and Marks cards; pill menus for aggregation, date levels, sorting
 * and quick table calculations; a formula editor; and a canvas that
 * recomputes through the viz core (under the caller's own access) on every
 * change. Everything a drag does is also reachable from a menu.
 */

const MARK_OPTIONS: { value: MarkType; label: string }[] = [
  { value: "bar", label: "Bar" },
  { value: "line", label: "Line" },
  { value: "area", label: "Area" },
  { value: "circle", label: "Circle" },
  { value: "square", label: "Square" },
  { value: "tick", label: "Tick" },
  { value: "pie", label: "Pie" },
  { value: "text", label: "Text" },
];

const DRAG_TYPE = "application/x-nova-pill";

type DragPayload = { from: "pane"; field: string } | { from: "pill"; location: PillLocation };

function readDrag(event: DragEvent): DragPayload | null {
  try {
    const raw = event.dataTransfer.getData(DRAG_TYPE);
    return raw ? (JSON.parse(raw) as DragPayload) : null;
  } catch {
    return null;
  }
}

function startDrag(event: DragEvent, payload: DragPayload) {
  event.dataTransfer.setData(DRAG_TYPE, JSON.stringify(payload));
  event.dataTransfer.effectAllowed = "move";
}

function acceptsDrag(event: DragEvent): boolean {
  return Array.from(event.dataTransfer.types).includes(DRAG_TYPE);
}

const RECORDS_ENTRY: DataPaneField = {
  name: RECORDS_FIELD,
  kind: "column",
  data_type: "number",
  role: "measure",
  level: "row",
};

export function WorksheetEditor({
  artifactId,
  spec,
  initialPresmodel,
  title,
  expectedUpdatedAt,
  onSaved,
  onCancel,
}: {
  artifactId: string;
  spec: WorksheetSpec;
  initialPresmodel: PresModel | null;
  title: string;
  expectedUpdatedAt: string;
  onSaved: (spec: WorksheetSpec) => void;
  onCancel: () => void;
}) {
  const [history, dispatch] = useReducer(historyReducer, spec, historyOf);
  const working = history.present;
  const apply = (next: WorksheetSpec) => dispatch({ type: "apply", spec: next });

  const [fields, setFields] = useState<DataPaneField[]>([]);
  const [sample, setSample] = useState<{ columns: string[]; rows: (string | number | null)[][] }>({
    columns: [],
    rows: [],
  });
  const [paneError, setPaneError] = useState<string | null>(null);
  const [presmodel, setPresmodel] = useState<PresModel | null>(initialPresmodel);
  const [sql, setSql] = useState("");
  const [computeError, setComputeError] = useState<string | null>(null);
  const [computing, setComputing] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [selectedMark, setSelectedMark] = useState<string | number | null>(null);
  const [suggestions, setSuggestions] = useState<ShowMeSuggestion[]>([]);
  const [calcDialog, setCalcDialog] = useState<{ open: boolean; calc: CalculatedField | null }>({
    open: false,
    calc: null,
  });
  const [filterDialog, setFilterDialog] = useState<{
    open: boolean;
    field: string | null;
    index?: number;
    asMeasure: boolean;
  }>({ open: false, field: null, asMeasure: false });

  useEffect(() => {
    dispatch({ type: "reset", spec });
    setPresmodel(initialPresmodel);
  }, [spec, initialPresmodel]);

  const source = useMemo<VizSourceBody | null>(
    () =>
      working.source
        ? { source: working.source, calculations: working.calculations ?? [], filters: working.filters ?? [] }
        : null,
    [working.source, working.calculations, working.filters],
  );

  // The data pane: re-described when the calculations change.
  const calcKey = JSON.stringify(working.calculations ?? []);
  useEffect(() => {
    if (!working.source) return;
    let cancelled = false;
    studioApi
      .describeVizSource({ source: working.source, calculations: working.calculations ?? [] })
      .then((described) => {
        if (cancelled) return;
        setFields(described.fields);
        setSample({ columns: described.sample_columns, rows: described.sample_rows });
        setPaneError(null);
      })
      .catch((error: Error) => {
        if (!cancelled) setPaneError(error.message);
      });
    return () => {
      cancelled = true;
    };
  }, [working.source, calcKey]);

  useEffect(() => {
    if (!sample.columns.length) return;
    let cancelled = false;
    studioApi
      .suggestViz({ columns: sample.columns, rows: sample.rows.slice(0, 50) })
      .then((response) => {
        if (!cancelled) setSuggestions(response.suggestions);
      })
      .catch(() => setSuggestions([]));
    return () => {
      cancelled = true;
    };
  }, [sample]);

  // Recompute the view after every change (debounced, latest wins).
  const sequence = useRef(0);
  useEffect(() => {
    if (!hasView(working) || !working.source) {
      setComputeError(null);
      setComputing(false);
      return;
    }
    const run = ++sequence.current;
    setComputing(true);
    const timer = window.setTimeout(() => {
      studioApi
        .computeViz({ spec: working as unknown as Record<string, unknown> })
        .then((response) => {
          if (run !== sequence.current) return;
          setPresmodel(response.presmodel);
          setSelectedMark(null);
          setSql(response.sql);
          setElapsedMs(response.elapsed_ms);
          if (response.fields?.length) setFields(response.fields);
          setComputeError(null);
        })
        .catch((error: Error) => {
          if (run === sequence.current) setComputeError(error.message);
        })
        .finally(() => {
          if (run === sequence.current) setComputing(false);
        });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [working]);

  const save = useMutation({
    mutationFn: (next: WorksheetSpec) =>
      studioApi.updateArtifactSpec(artifactId, {
        viz_spec: next as unknown as Record<string, unknown>,
        expected_updated_at: expectedUpdatedAt,
      }),
    onSuccess: () => {
      toast.success("Sheet saved");
      onSaved(working);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const fieldByName = useMemo(() => {
    const map = new Map<string, DataPaneField>(fields.map((field) => [field.name, field]));
    map.set(RECORDS_FIELD, RECORDS_ENTRY);
    return map;
  }, [fields]);

  const pillForField = (name: string): FieldRef | null => {
    if (name === RECORDS_FIELD) return recordsPill();
    const field = fieldByName.get(name);
    return field ? pillFromField(field) : null;
  };

  const dropOn = (target: Shelf | Encoding, event: DragEvent, index?: number) => {
    event.preventDefault();
    event.stopPropagation();
    const payload = readDrag(event);
    if (!payload) return;
    if (payload.from === "pane") {
      const pill = pillForField(payload.field);
      if (pill) apply(placePill(working, target, pill, index));
    } else {
      apply(movePill(working, payload.location, target, index));
    }
  };

  const dropOnFilters = (event: DragEvent) => {
    event.preventDefault();
    const payload = readDrag(event);
    if (!payload) return;
    const pill =
      payload.from === "pane" ? pillForField(payload.field) : pillAt(working, payload.location);
    if (!pill || pill.field === RECORDS_FIELD) return;
    openFilter(pill.field, isMeasure(pill) && pill.aggregation !== "agg", undefined, pill);
  };

  const openFilter = (field: string, asMeasure: boolean, index?: number, pill?: FieldRef) => {
    const entry = fieldByName.get(field);
    const measure = asMeasure || entry?.level === "aggregate";
    if (entry?.level === "table") {
      toast.error("Table calculations cannot be filtered yet.");
      return;
    }
    if (pill && measure && pill.aggregation && pill.aggregation !== "agg") {
      setFilterDialog({ open: true, field, index, asMeasure: true });
      return;
    }
    setFilterDialog({ open: true, field, index, asMeasure: measure });
  };

  const dims = viewDimensions(working);
  const canUndo = history.past.length > 0;
  const canRedo = history.future.length > 0;

  const onKeyDown = (event: React.KeyboardEvent) => {
    const mod = event.metaKey || event.ctrlKey;
    if (!mod || (event.target as HTMLElement).closest(".monaco-editor, input, textarea")) return;
    if (event.key.toLowerCase() === "z") {
      event.preventDefault();
      dispatch({ type: event.shiftKey ? "redo" : "undo" });
    } else if (event.key.toLowerCase() === "y") {
      event.preventDefault();
      dispatch({ type: "redo" });
    }
  };

  const filterInitial =
    filterDialog.index !== undefined ? (working.filters ?? [])[filterDialog.index] ?? null : null;

  const insight = presmodel && !computeError ? describeView(presmodel, selectedMark) : null;
  const recommended = suggestions.find((suggestion) => suggestion.recommended)?.id ?? null;

  return (
    <div className="viz-numerals flex min-w-0 flex-col overflow-hidden rounded-lg border bg-card" onKeyDown={onKeyDown}>
      <div className="flex flex-wrap items-center gap-1.5 border-b px-3 py-2" role="toolbar" aria-label="Worksheet tools">
        <ToolButton label="Undo" onClick={() => dispatch({ type: "undo" })} disabled={!canUndo}>
          <Undo2 aria-hidden="true" className="size-4" />
        </ToolButton>
        <ToolButton label="Redo" onClick={() => dispatch({ type: "redo" })} disabled={!canRedo}>
          <Redo2 aria-hidden="true" className="size-4" />
        </ToolButton>
        <ToolButton label="Swap rows and columns" onClick={() => apply(swapShelves(working))} disabled={!hasView(working)}>
          <ArrowDownUp aria-hidden="true" className="size-4" />
        </ToolButton>
        <ToolButton label="Clear sheet" onClick={() => apply(clearSheet(working))} disabled={!hasView(working)}>
          <Trash2 aria-hidden="true" className="size-4" />
        </ToolButton>
        <div className="mx-1 h-5 w-px bg-border" aria-hidden="true" />
        <Input
          value={working.title ?? ""}
          onChange={(event) => apply({ ...working, title: event.target.value.slice(0, 256) })}
          placeholder={title || "Sheet title"}
          aria-label="Sheet title"
          className="type-ui h-8 w-full min-w-0 sm:w-72"
        />
        <div className="ml-auto flex items-center gap-2">
          <Button type="button" variant="outline" size="sm" className="type-ui" onClick={onCancel} disabled={save.isPending}>
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            className="type-ui font-semibold"
            onClick={() => save.mutate(working)}
            disabled={save.isPending || !hasView(working) || Boolean(computeError)}
          >
            {save.isPending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : null}
            Save sheet
          </Button>
        </div>
      </div>

      <div className="flex min-w-0 flex-col lg:flex-row">
        <DataPane
          fields={fields}
          error={paneError}
          used={new Set([...dims.map((d) => d.field), ...working.columns.concat(working.rows).map((p) => p.field)])}
          onAdd={(name, target) => {
            const pill = pillForField(name);
            if (!pill) return;
            if (target === "filters") openFilter(name, false);
            else apply(placePill(working, target, pill));
          }}
          onCreateCalc={() => setCalcDialog({ open: true, calc: null })}
          onEditCalc={(name) => {
            const calc = (working.calculations ?? []).find((item) => item.name === name) ?? null;
            setCalcDialog({ open: true, calc });
          }}
          onDuplicateCalc={(name) => {
            const calc = (working.calculations ?? []).find((item) => item.name === name);
            if (!calc) return;
            let copy = `${calc.name} (copy)`;
            for (let n = 2; fieldByName.has(copy); n += 1) copy = `${calc.name} (copy ${n})`;
            apply(upsertCalculation(working, { ...calc, name: copy }));
          }}
          onDeleteCalc={(name) => {
            const dependents = calculationDependents(working, name);
            if (dependents.length) {
              toast.error(`${name} is used by ${dependents.join(", ")}. Change those first.`);
              return;
            }
            apply(removeCalculation(working, name));
          }}
          onDropPill={(event) => {
            event.preventDefault();
            const payload = readDrag(event);
            if (payload?.from === "pill") apply(removePill(working, payload.location));
          }}
        />

        <div className="flex w-full min-w-0 shrink-0 flex-col border-b lg:w-60 lg:border-r lg:border-b-0">
          <RailSection title="Filters">
            <div
              className="flex min-h-11 flex-col gap-1 rounded-md border border-dashed p-1.5"
              aria-label="Filters shelf"
              onDragOver={(event) => {
                if (acceptsDrag(event)) event.preventDefault();
              }}
              onDrop={dropOnFilters}
            >
              {(working.filters ?? []).map((filter, index) => (
                <FilterPill
                  key={`${filter.field}-${index}`}
                  filter={filter}
                  onEdit={() => openFilter(filter.field, filter.kind === "quantitative" && Boolean(filter.aggregation), index)}
                  onToggleContext={() => apply(upsertFilter(working, { ...filter, context: !filter.context } as Filter, index))}
                  onRemove={() => apply(removeFilter(working, index))}
                />
              ))}
              {!(working.filters ?? []).length ? (
                <span className="type-meta px-1 py-1.5">Drop a field to filter</span>
              ) : null}
            </div>
          </RailSection>

          <RailSection title="Marks">
            <Select
              value={working.marks.type}
              onValueChange={(value) => apply({ ...working, marks: { ...working.marks, type: value as MarkType } })}
            >
              <SelectTrigger aria-label="Mark type" className="type-ui h-8 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MARK_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="mt-2 grid grid-cols-2 gap-1.5">
              {(["color", "size", "text", "detail"] as Encoding[])
                .concat(working.marks.type === "pie" ? ["angle"] : [])
                .map((encoding) => (
                  <EncodingSlot
                    key={encoding}
                    encoding={encoding}
                    pill={working.marks[encoding] ?? null}
                    onDrop={(event) => dropOn(encoding, event)}
                    renderPill={(pill) => (
                      <PillChip
                        pill={pill}
                        location={{ target: encoding, index: 0 }}
                        working={working}
                        field={fieldByName.get(pill.field)}
                        apply={apply}
                        onFilter={(asMeasure) => openFilter(pill.field, asMeasure, undefined, pill)}
                        onEditCalc={(name) =>
                          setCalcDialog({
                            open: true,
                            calc: (working.calculations ?? []).find((item) => item.name === name) ?? null,
                          })
                        }
                      />
                    )}
                  />
                ))}
            </div>
          </RailSection>

          {suggestions.length ? (
            <RailSection title="Show Me">
              <div className="grid grid-cols-3 gap-1">
                {suggestions.map((suggestion) => {
                  const best = suggestion.id === recommended;
                  return (
                    <button
                      key={suggestion.id}
                      type="button"
                      disabled={!suggestion.enabled}
                      aria-label={`Show ${suggestion.label}`}
                      title={
                        suggestion.enabled
                          ? best
                            ? `${suggestion.label}: the best fit for these fields`
                            : `Show ${suggestion.label}`
                          : `Needs ${suggestion.unmet.map((item) => item.kind).join(", ")}`
                      }
                      onClick={() => apply(applyShowMe(working, suggestion.id, fields))}
                      className={cn(
                        "type-meta flex flex-col items-center gap-0.5 rounded-md border px-1 py-1.5 transition-colors focus-visible:outline-2 focus-visible:outline-ring",
                        best
                          ? "border-primary bg-accent text-accent-foreground"
                          : suggestion.enabled
                            ? "text-ink-secondary hover:border-viz-axis hover:bg-surface-1"
                            : "cursor-not-allowed opacity-45",
                      )}
                    >
                      <ShowMeIcon id={suggestion.id} />
                      <span className="max-w-full truncate">{suggestion.label}</span>
                    </button>
                  );
                })}
              </div>
            </RailSection>
          ) : null}
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="border-b px-5 py-2">
            {(["columns", "rows"] as Shelf[]).map((shelf) => (
              <ShelfRow
                key={shelf}
                shelf={shelf}
                pills={working[shelf]}
                onDrop={(event, index) => dropOn(shelf, event, index)}
                renderPill={(pill, index) => (
                  <PillChip
                    pill={pill}
                    location={{ target: shelf, index }}
                    working={working}
                    field={fieldByName.get(pill.field)}
                    apply={apply}
                    onFilter={(asMeasure) => openFilter(pill.field, asMeasure, undefined, pill)}
                    onEditCalc={(name) =>
                      setCalcDialog({
                        open: true,
                        calc: (working.calculations ?? []).find((item) => item.name === name) ?? null,
                      })
                    }
                  />
                )}
              />
            ))}
          </div>

          <section className="relative min-h-80 flex-1 px-6 py-6 sm:px-8" aria-label="Viz canvas" aria-busy={computing}>
            {computing ? (
              <div className="type-meta absolute top-3 right-4 z-10 flex items-center gap-1.5" role="status">
                <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
                Computing
              </div>
            ) : null}
            {!hasView(working) ? (
              <div className="flex min-h-64 flex-col items-center justify-center gap-1 text-center">
                <p className="type-title">Build a view</p>
                <p className="type-body max-w-sm">Drag fields from the data pane to Columns and Rows, or pick a chart in Show Me.</p>
              </div>
            ) : computeError ? (
              <div className="flex min-h-64 flex-col items-center justify-center gap-1 text-center" role="alert">
                <p className="type-title">This view cannot be computed</p>
                <p className="type-ui max-w-md text-destructive">{computeError}</p>
              </div>
            ) : presmodel ? (
              <div className={cn("flex flex-col gap-6", computing && "opacity-60")}>
                <ViewAnswer question={working.title || title} insight={insight} />
                <VizView
                  presmodel={presmodel}
                  highlight={insight?.highlight ?? null}
                  onMarkClick={(values) => {
                    const dimension = insight?.highlight?.dimension;
                    if (dimension && dimension in values) setSelectedMark(values[dimension]);
                  }}
                />
              </div>
            ) : (
              <div className="flex min-h-64 items-center justify-center">
                <Loader2 aria-hidden="true" className="size-5 animate-spin text-muted-foreground" />
                <span className="sr-only">Computing the view</span>
              </div>
            )}
          </section>

          {sql && showSql ? (
            <pre className="type-code max-h-64 overflow-auto border-t bg-surface-1 px-5 py-3 whitespace-pre-wrap">{sql}</pre>
          ) : null}
          <footer className="type-meta flex flex-wrap items-center gap-x-4 gap-y-1 border-t bg-surface-1 px-5 py-2">
            {presmodel && hasView(working) && !computeError ? (
              <>
                <span>{presmodel.row_count.toLocaleString()} {presmodel.row_count === 1 ? "mark" : "marks"}</span>
                {elapsedMs !== null ? <span>computed under your access in {Math.round(elapsedMs)} ms</span> : null}
              </>
            ) : (
              <span>Nothing computed yet</span>
            )}
            {sql ? (
              <button
                type="button"
                className="ml-auto inline-flex items-center gap-1 rounded hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
                aria-expanded={showSql}
                onClick={() => setShowSql((value) => !value)}
              >
                <Code2 aria-hidden="true" className="size-3.5" />
                Generated SQL
                <ChevronDown aria-hidden="true" className={cn("size-3.5 transition-transform", showSql && "rotate-180")} />
              </button>
            ) : null}
          </footer>
        </div>
      </div>

      {source ? (
        <CalcEditor
          open={calcDialog.open}
          onOpenChange={(open) => setCalcDialog((current) => ({ ...current, open }))}
          source={source}
          initial={calcDialog.calc}
          fields={fields}
          viewDimensions={dims}
          onSave={(calc, previous) => apply(upsertCalculation(working, calc, previous))}
        />
      ) : null}
      {source ? (
        <FilterDialog
          open={filterDialog.open}
          onOpenChange={(open) => setFilterDialog((current) => ({ ...current, open }))}
          source={source}
          field={filterDialog.field ? fieldByName.get(filterDialog.field) ?? null : null}
          initial={filterInitial}
          asMeasure={filterDialog.asMeasure}
          onSave={(filter) => apply(upsertFilter(working, filter, filterDialog.index))}
        />
      ) : null}
    </div>
  );
}

/** The view's answer, set as the headline: question, finding, one sentence. */
export function ViewAnswer({ question, insight }: { question: string; insight: ViewInsight | null }) {
  return (
    <div className="max-w-[64ch]">
      {question ? <h2 className="type-question">{question}</h2> : null}
      {insight ? (
        <>
          <p className="type-answer mt-2 text-foreground">
            <span className={insight.lead.marked ? "text-viz-highlight" : undefined}>{insight.lead.subject}</span> {insight.lead.verb} {insight.lead.num}
            {insight.lead.unit ? <span className="type-unit">{insight.lead.unit}</span> : null}
          </p>
          {insight.detail ? <p className="type-body mt-2">{insight.detail}</p> : null}
        </>
      ) : null}
    </div>
  );
}

function RailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b px-3 py-3 last:border-b-0" aria-label={title}>
      <h3 className="type-label mb-2">{title}</h3>
      {children}
    </section>
  );
}

const SHOW_ME_ICONS: Record<string, string> = {
  text_table: "M3 4h18v12H3zM3 10h18M10 4v12",
  heatmap: "M3 4h6v6H3zM9 4h6v6H9zM15 4h6v6h-6zM3 10h6v6H3zM9 10h6v6H9zM15 10h6v6h-6z",
  bar: "M3 17h18M5 17V9M10 17V4M15 17V11M20 17V14",
  bar_stacked: "M3 17h18M6 17V6M6 11h4M12 17V9M12 12h4M18 17V12",
  bar_side: "M3 17h18M5 17V8M8 17V12M13 17V5M16 17V10",
  line: "M3 15l5-5 4 3 8-8",
  area: "M3 15l5-5 4 3 8-8v12H3z",
  scatter: "M6 14h.01M10 8h.01M14 12h.01M18 5h.01M8 11h.01",
  pie: "M12 4a7 7 0 1 0 7 7h-7z",
  bubble: "M7 13a2 2 0 1 0 0.01 0M14 8a3.5 3.5 0 1 0 0.01 0M17 15a1.5 1.5 0 1 0 0.01 0",
};

function ShowMeIcon({ id }: { id: string }) {
  return (
    <svg width="24" height="20" viewBox="0 0 24 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
      <path d={SHOW_ME_ICONS[id] ?? SHOW_ME_ICONS.bar} />
    </svg>
  );
}

function ToolButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-8"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
    >
      {children}
    </Button>
  );
}

function FieldGlyph({ field }: { field: DataPaneField }) {
  const glyph =
    field.kind === "calculation"
      ? "="
      : field.data_type === "number"
        ? "#"
        : isTemporal(field)
          ? "Date"
          : field.data_type === "boolean"
            ? "T|F"
            : "Abc";
  return (
    <span
      aria-hidden="true"
      className={cn(
        "type-meta w-8 shrink-0",
        field.role === "measure" ? "text-pill-continuous-foreground" : "text-pill-discrete-foreground",
      )}
    >
      {glyph}
    </span>
  );
}

function DataPane({
  fields,
  error,
  used,
  onAdd,
  onCreateCalc,
  onEditCalc,
  onDuplicateCalc,
  onDeleteCalc,
  onDropPill,
}: {
  fields: DataPaneField[];
  error: string | null;
  used: Set<string>;
  onAdd: (name: string, target: Shelf | Encoding | "filters") => void;
  onCreateCalc: () => void;
  onEditCalc: (name: string) => void;
  onDuplicateCalc: (name: string) => void;
  onDeleteCalc: (name: string) => void;
  onDropPill: (event: DragEvent) => void;
}) {
  const [search, setSearch] = useState("");
  const query = search.trim().toLowerCase();
  const visible = fields.filter((field) => !query || field.name.toLowerCase().includes(query));
  const dimensions = visible.filter((field) => field.role === "dimension");
  const measures = visible.filter((field) => field.role === "measure");
  return (
    <aside
      aria-label="Data pane"
      className="flex w-full min-w-0 shrink-0 flex-col gap-3 border-b bg-surface-1 px-3 py-3 lg:max-h-[46rem] lg:w-60 lg:border-r lg:border-b-0"
      onDragOver={(event) => {
        if (acceptsDrag(event)) event.preventDefault();
      }}
      onDrop={onDropPill}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="type-label">Data</h3>
        <Button type="button" variant="ghost" size="sm" className="type-meta h-7 gap-1 px-2 text-ink-secondary" onClick={onCreateCalc}>
          <Plus aria-hidden="true" className="size-3.5" />
          Calculation
        </Button>
      </div>
      <div className="relative">
        <Search aria-hidden="true" className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search fields"
          aria-label="Search fields"
          className="type-ui h-8 bg-card pl-7"
        />
      </div>
      {error ? <p className="type-meta text-destructive">{error}</p> : null}
      {!fields.length && !error ? (
        <p className="type-meta flex items-center gap-1.5" role="status">
          <Loader2 aria-hidden="true" className="size-3 animate-spin" /> Reading fields
        </p>
      ) : null}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <FieldGroup title="Dimensions" fields={dimensions} used={used} {...{ onAdd, onEditCalc, onDuplicateCalc, onDeleteCalc }} />
        <FieldGroup
          title="Measures"
          fields={query && !"records".includes(query) ? measures : [...measures, RECORDS_ENTRY]}
          used={used}
          {...{ onAdd, onEditCalc, onDuplicateCalc, onDeleteCalc }}
        />
      </div>
    </aside>
  );
}

function FieldGroup({
  title,
  fields,
  used,
  onAdd,
  onEditCalc,
  onDuplicateCalc,
  onDeleteCalc,
}: {
  title: string;
  fields: DataPaneField[];
  used: Set<string>;
  onAdd: (name: string, target: Shelf | Encoding | "filters") => void;
  onEditCalc: (name: string) => void;
  onDuplicateCalc: (name: string) => void;
  onDeleteCalc: (name: string) => void;
}) {
  if (!fields.length) return null;
  return (
    <div className="mb-3">
      <p className="type-label px-1 pb-1">{title}</p>
      <ul>
        {fields.map((field) => {
          const label = field.name === RECORDS_FIELD ? "Records (count)" : field.name;
          return (
            <li
              key={field.name}
              draggable
              onDragStart={(event) => startDrag(event, { from: "pane", field: field.name })}
              className={cn(
                "group flex h-7 cursor-grab items-center gap-1 rounded px-1 hover:bg-muted active:cursor-grabbing",
                used.has(field.name) && "bg-muted",
              )}
            >
              <FieldGlyph field={field} />
              <span
                className={cn(
                  "min-w-0 flex-1 truncate",
                  field.kind === "column" && field.name !== RECORDS_FIELD ? "type-code" : "type-ui",
                )}
                title={field.formula ? `${label} = ${field.formula}` : label}
              >
                {label}
              </span>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    aria-label={`Actions for ${label}`}
                    className="rounded p-0.5 text-muted-foreground opacity-60 group-hover:opacity-100 hover:text-foreground focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-ring"
                  >
                    <ChevronDown aria-hidden="true" className="size-3.5" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-48">
                  <DropdownMenuLabel className="type-meta">Add to</DropdownMenuLabel>
                  {(["columns", "rows"] as const).map((target) => (
                    <DropdownMenuItem key={target} onSelect={() => onAdd(field.name, target)}>
                      {target === "columns" ? "Columns" : "Rows"}
                    </DropdownMenuItem>
                  ))}
                  {field.name !== RECORDS_FIELD ? (
                    <DropdownMenuItem onSelect={() => onAdd(field.name, "filters")}>Filters</DropdownMenuItem>
                  ) : null}
                  {(["color", "size", "text", "detail"] as Encoding[]).map((target) => (
                    <DropdownMenuItem key={target} onSelect={() => onAdd(field.name, target)}>
                      {ENCODING_LABELS[target]}
                    </DropdownMenuItem>
                  ))}
                  {field.kind === "calculation" ? (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onSelect={() => onEditCalc(field.name)}>
                        <Equal aria-hidden="true" className="size-3.5" /> Edit calculation
                      </DropdownMenuItem>
                      <DropdownMenuItem onSelect={() => onDuplicateCalc(field.name)}>
                        <Copy aria-hidden="true" className="size-3.5" /> Duplicate
                      </DropdownMenuItem>
                      <DropdownMenuItem variant="destructive" onSelect={() => onDeleteCalc(field.name)}>
                        <Trash2 aria-hidden="true" className="size-3.5" /> Delete
                      </DropdownMenuItem>
                    </>
                  ) : null}
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ShelfRow({
  shelf,
  pills,
  onDrop,
  renderPill,
}: {
  shelf: Shelf;
  pills: FieldRef[];
  onDrop: (event: DragEvent, index?: number) => void;
  renderPill: (pill: FieldRef, index: number) => ReactNode;
}) {
  const [over, setOver] = useState(false);
  const label = shelf === "columns" ? "Columns" : "Rows";
  return (
    <div className="flex min-h-9 items-center gap-3 py-0.5">
      <span className="type-meta w-16 shrink-0">{label}</span>
      <div
        className={cn(
          "flex min-h-8 flex-1 flex-wrap items-center gap-1.5 rounded-md border border-transparent px-1 py-0.5 transition-colors",
          !pills.length && "border-dashed border-border",
          over && "border-ring bg-surface-1",
        )}
        aria-label={`${label} shelf`}
        onDragOver={(event) => {
          if (!acceptsDrag(event)) return;
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          setOver(false);
          onDrop(event);
        }}
      >
        {pills.map((pill, index) => (
          <span
            key={`${pill.field}-${pill.aggregation ?? ""}-${pill.date_part ?? ""}-${index}`}
            onDragOver={(event) => {
              if (acceptsDrag(event)) event.preventDefault();
            }}
            onDrop={(event) => {
              setOver(false);
              onDrop(event, index);
            }}
          >
            {renderPill(pill, index)}
          </span>
        ))}
        {!pills.length ? <span className="type-meta px-1">Drop fields here</span> : null}
      </div>
    </div>
  );
}

function EncodingSlot({
  encoding,
  pill,
  onDrop,
  renderPill,
}: {
  encoding: Encoding;
  pill: FieldRef | null;
  onDrop: (event: DragEvent) => void;
  renderPill: (pill: FieldRef) => ReactNode;
}) {
  const [over, setOver] = useState(false);
  return (
    <div
      className={cn(
        "flex min-h-12 flex-col gap-1 rounded-md border border-dashed p-1.5 transition-colors",
        over && "border-ring bg-surface-1",
        pill && "col-span-2",
      )}
      aria-label={`${ENCODING_LABELS[encoding]} encoding`}
      onDragOver={(event) => {
        if (!acceptsDrag(event)) return;
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        setOver(false);
        onDrop(event);
      }}
    >
      <span className="type-meta">{ENCODING_LABELS[encoding]}</span>
      {pill ? renderPill(pill) : null}
    </div>
  );
}

function FilterPill({
  filter,
  onEdit,
  onToggleContext,
  onRemove,
}: {
  filter: Filter;
  onEdit: () => void;
  onToggleContext: () => void;
  onRemove: () => void;
}) {
  const measure = filter.kind === "quantitative" && Boolean(filter.aggregation);
  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={onEdit}
        title={filterSummary(filter)}
        className={cn(
          "flex h-6 min-w-0 flex-1 items-center gap-1.5 rounded border px-2 text-left focus-visible:outline-2 focus-visible:outline-ring",
          measure
            ? "border-pill-continuous-border bg-pill-continuous text-pill-continuous-foreground"
            : "border-pill-discrete-border bg-pill-discrete text-pill-discrete-foreground",
        )}
      >
        <FilterIcon aria-hidden="true" className="size-3 shrink-0" />
        <span className="type-code truncate">{filterCaption(filter)}</span>
        {filter.context ? <span className="type-meta shrink-0 text-current">context</span> : null}
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={`Filter actions for ${filter.field}`}
            className="rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
          >
            <ChevronDown aria-hidden="true" className="size-3" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={onEdit}>Edit filter</DropdownMenuItem>
          {!measure ? (
            <DropdownMenuItem onSelect={onToggleContext}>
              {filter.context ? "Remove from context" : "Add to context"}
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={onRemove}>
            Remove
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

/** A field pill: blue for discrete headers, green for continuous axes. The
 * operator (SUM, YEAR) is set in the interface face, the field in mono. */
export function Pill({ pill }: { pill: FieldRef }) {
  const { prefix, name } = pillParts(pill);
  return (
    <span
      className={cn(
        "inline-flex h-6 max-w-full items-center gap-1 rounded border px-2",
        isContinuous(pill)
          ? "border-pill-continuous-border bg-pill-continuous text-pill-continuous-foreground"
          : "border-pill-discrete-border bg-pill-discrete text-pill-discrete-foreground",
      )}
    >
      {prefix ? <span className="type-label">{prefix}</span> : null}
      <span className={cn("truncate", pill.field === RECORDS_FIELD ? "type-ui" : "type-code")}>{name}</span>
    </span>
  );
}

function PillChip({
  pill,
  location,
  working,
  field,
  apply,
  onFilter,
  onEditCalc,
}: {
  pill: FieldRef;
  location: PillLocation;
  working: WorksheetSpec;
  field: DataPaneField | undefined;
  apply: (next: WorksheetSpec) => void;
  onFilter: (asMeasure: boolean) => void;
  onEditCalc: (name: string) => void;
}) {
  const measure = isMeasure(pill);
  const calcAggregate = pill.aggregation === "agg";
  const temporal = field ? isTemporal(field) : pill.field_type === "temporal" || Boolean(pill.date_part);
  const numeric = field?.data_type === "number" || pill.field === RECORDS_FIELD;
  const direction = sortDirection(working, pill);
  const quick = quickTableCalc(working, pill);
  const change = (fn: (current: FieldRef) => FieldRef) => apply(updatePill(working, location, fn));
  return (
    <span className="inline-flex items-center">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            draggable
            onDragStart={(event) => startDrag(event, { from: "pill", location })}
            className="cursor-grab rounded focus-visible:outline-2 focus-visible:outline-ring active:cursor-grabbing"
            aria-label={`${pillCaption(pill)} options`}
          >
            <Pill pill={pill} />
            {direction ? <span className="sr-only">, sorted {direction === "asc" ? "ascending" : "descending"}</span> : null}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          {measure && !calcAggregate && pill.field !== RECORDS_FIELD ? (
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>Measure ({AGGREGATION_LABELS[pill.aggregation as Exclude<Aggregation, "agg">]})</DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-52">
                <DropdownMenuRadioGroup
                  value={pill.aggregation ?? ""}
                  onValueChange={(value) => change((current) => setAggregation(current, value as Aggregation))}
                >
                  {(Object.keys(AGGREGATION_LABELS) as Exclude<Aggregation, "agg">[])
                    .filter((item) => numeric || !NUMERIC_AGGREGATIONS.has(item))
                    .map((item) => (
                      <DropdownMenuRadioItem key={item} value={item}>
                        {AGGREGATION_LABELS[item]}
                      </DropdownMenuRadioItem>
                    ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          ) : null}
          {measure && !calcAggregate && pill.field !== RECORDS_FIELD ? (
            <DropdownMenuItem onSelect={() => change((current) => asDimension(current, field))}>Dimension</DropdownMenuItem>
          ) : null}
          {!measure && temporal ? (
            <>
              <DropdownMenuLabel className="text-xs">Discrete date</DropdownMenuLabel>
              {DATE_PARTS.map((part) => (
                <DropdownMenuItem
                  key={`d-${part.value}`}
                  onSelect={() => change((current) => setDatePart(current, part.value, true))}
                  aria-checked={pill.discrete && pill.date_part === part.value}
                  className={cn(pill.discrete && pill.date_part === part.value && "font-medium")}
                >
                  {part.label}
                </DropdownMenuItem>
              ))}
              <DropdownMenuLabel className="text-xs">Continuous date</DropdownMenuLabel>
              {DATE_PARTS.map((part) => (
                <DropdownMenuItem
                  key={`c-${part.value}`}
                  onSelect={() => change((current) => setDatePart(current, part.value, false))}
                  aria-checked={!pill.discrete && pill.date_part === part.value}
                  className={cn(!pill.discrete && pill.date_part === part.value && "font-medium")}
                >
                  {part.label}
                </DropdownMenuItem>
              ))}
              <DropdownMenuItem onSelect={() => change((current) => setDatePart(current, null, true))}>
                Exact date
              </DropdownMenuItem>
              <DropdownMenuSeparator />
            </>
          ) : null}
          {!measure && !temporal ? (
            <DropdownMenuItem onSelect={() => change(toggleDiscrete)}>
              {pill.discrete ? "Continuous" : "Discrete"}
            </DropdownMenuItem>
          ) : null}
          {!measure && field?.level !== "aggregate" && field?.level !== "table" ? (
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>Measure</DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-52">
                {(Object.keys(AGGREGATION_LABELS) as Exclude<Aggregation, "agg">[])
                  .filter((item) => numeric || !NUMERIC_AGGREGATIONS.has(item))
                  .map((item) => (
                    <DropdownMenuItem key={item} onSelect={() => change((current) => setAggregation(current, item))}>
                      {AGGREGATION_LABELS[item]}
                    </DropdownMenuItem>
                  ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          ) : null}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => apply(setSort(working, pill, direction === "asc" ? null : "asc"))}>
            {direction === "asc" ? "Clear ascending sort" : "Sort ascending"}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => apply(setSort(working, pill, direction === "desc" ? null : "desc"))}>
            {direction === "desc" ? "Clear descending sort" : "Sort descending"}
          </DropdownMenuItem>
          {measure && field?.level !== "table" ? (
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>Quick table calculation</DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-48">
                {QUICK_TABLE_CALCS.map((item) => (
                  <DropdownMenuItem
                    key={item.kind}
                    onSelect={() => apply(setQuickTableCalc(working, pill, item.kind))}
                    className={cn(quick === item.kind && "font-medium")}
                  >
                    {item.label}
                  </DropdownMenuItem>
                ))}
                {quick ? (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={() => apply(setQuickTableCalc(working, pill, null))}>Clear</DropdownMenuItem>
                  </>
                ) : null}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          ) : null}
          {pill.field !== RECORDS_FIELD && field?.level !== "table" ? (
            <DropdownMenuItem onSelect={() => onFilter(measure && !calcAggregate)}>
              <FilterIcon aria-hidden="true" className="size-3.5" /> Filter…
            </DropdownMenuItem>
          ) : null}
          {field?.kind === "calculation" ? (
            <DropdownMenuItem onSelect={() => onEditCalc(field.name)}>
              <Equal aria-hidden="true" className="size-3.5" />
              {field.level === "table" ? "Edit calculation and direction" : "Edit calculation"}
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={() => apply(removePill(working, location))}>
            <X aria-hidden="true" className="size-3.5" /> Remove
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </span>
  );
}
