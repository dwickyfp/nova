import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { studioApi } from "@/features/agents/api";
import { cn } from "@/lib/utils";
import { VizView } from "./viz-view";
import type {
  Aggregation,
  FieldRef,
  MarkType,
  PresModel,
  ShowMeSuggestion,
  WorksheetSpec,
} from "./types";

/**
 * VizEditor is Tableau's authoring surface on Nova tokens: a data pane, the
 * column/row shelves, the marks card, filters, and a ShowMe strip. Pills are
 * Tableau's blue (discrete) and green (continuous); every change re-compiles
 * the worksheet through the viz core and redraws the canvas.
 */

const MARK_OPTIONS: { value: MarkType; label: string }[] = [
  { value: "bar", label: "Bar" },
  { value: "line", label: "Line" },
  { value: "area", label: "Area" },
  { value: "circle", label: "Circle" },
  { value: "square", label: "Square" },
  { value: "tick", label: "Tick" },
  { value: "pie", label: "Pie" },
  { value: "text", label: "Text table" },
];

export function VizEditor({
  artifactId,
  spec,
  initialPresmodel,
  columns,
  sampleRows,
  title,
  expectedUpdatedAt,
  onSaved,
  savingLabel = "Save view",
}: {
  artifactId: string;
  spec: WorksheetSpec;
  initialPresmodel: PresModel;
  columns: string[];
  sampleRows: (string | number | null)[][];
  title: string;
  expectedUpdatedAt: string;
  onSaved: (spec: WorksheetSpec) => void;
  savingLabel?: string;
}) {
  const [working, setWorking] = useState<WorksheetSpec>(spec);
  const [presmodel, setPresmodel] = useState<PresModel>(initialPresmodel);
  const [suggestions, setSuggestions] = useState<ShowMeSuggestion[]>([]);

  useEffect(() => {
    setWorking(spec);
    setPresmodel(initialPresmodel);
  }, [spec, initialPresmodel]);

  useEffect(() => {
    let cancelled = false;
    studioApi
      .suggestViz({ columns, rows: sampleRows.slice(0, 50) })
      .then((response) => {
        if (!cancelled) setSuggestions(response.suggestions);
      })
      .catch(() => setSuggestions([]));
    return () => {
      cancelled = true;
    };
  }, [columns, sampleRows]);

  const compute = useMutation({
    mutationFn: (next: WorksheetSpec) =>
      studioApi.computeViz({ spec: next as unknown as Record<string, unknown> }),
    onSuccess: (response) => setPresmodel(response.presmodel),
    onError: (error: Error) => toast.error(error.message),
  });

  const save = useMutation({
    mutationFn: (next: WorksheetSpec) =>
      studioApi.updateArtifactSpec(artifactId, {
        viz_spec: next as unknown as Record<string, unknown>,
        expected_updated_at: expectedUpdatedAt,
      }),
    onSuccess: () => {
      toast.success("Chart view saved");
      onSaved(working);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const apply = (next: WorksheetSpec) => {
    setWorking(next);
    compute.mutate(next);
  };

  const fields = useMemo(
    () => classify(columns, sampleRows),
    [columns, sampleRows],
  );
  const usedFields = new Set([
    ...working.columns.map((pill) => pill.field),
    ...working.rows.map((pill) => pill.field),
    ...(working.marks.color ? [working.marks.color.field] : []),
    ...(working.marks.size ? [working.marks.size.field] : []),
  ]);

  return (
    <div className="flex min-w-0 flex-col gap-3 lg:flex-row">
      <aside
        aria-label="Data pane"
        className="w-full shrink-0 rounded-xl border bg-card p-3 lg:w-56"
      >
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Data
        </h3>
        <ul className="flex flex-col gap-0.5">
          {fields.map((field) => (
            <li key={field.field}>
              <button
                type="button"
                onClick={() => addField(working, field, apply)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
                aria-label={`Add ${field.field} to the view`}
              >
                <Plus aria-hidden="true" className="size-3 shrink-0 text-muted-foreground" />
                <Pill pill={field} />
              </button>
            </li>
          ))}
        </ul>
        {suggestions.length ? (
          <div className="mt-4 border-t pt-3">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Show Me
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion.id}
                  type="button"
                  disabled={!suggestion.enabled}
                  title={
                    suggestion.enabled
                      ? `Show ${suggestion.label}`
                      : `Needs ${suggestion.unmet
                          .map((item) => item.kind)
                          .join(", ")}`
                  }
                  onClick={() => applyShowMe(working, suggestion.id, fields, apply)}
                  className={cn(
                    "rounded-md border px-2 py-1 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-ring",
                    suggestion.enabled
                      ? "hover:border-ring hover:bg-muted"
                      : "cursor-not-allowed opacity-40",
                  )}
                >
                  {suggestion.label}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="rounded-xl border bg-card p-3">
          <Shelf
            label="Columns"
            pills={working.columns}
            onRemove={(pill) => apply(removePill(working, "columns", pill))}
            onCycle={(pill) => apply(cyclePill(working, "columns", pill))}
          />
          <Shelf
            label="Rows"
            pills={working.rows}
            onRemove={(pill) => apply(removePill(working, "rows", pill))}
            onCycle={(pill) => apply(cyclePill(working, "rows", pill))}
            className="mt-2"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2 border-t pt-2">
            <span className="text-xs font-medium text-muted-foreground">Marks</span>
            <Select
              value={working.marks.type}
              onValueChange={(value) =>
                apply({ ...working, marks: { ...working.marks, type: value as MarkType } })
              }
            >
              <SelectTrigger
                aria-label="Mark type"
                className="h-7 w-32 border-none bg-muted text-xs"
              >
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
            <EncodingSlot
              name="Color"
              pill={working.marks.color ?? null}
              fields={fields}
              unusedOnly
              onPick={(pill) =>
                apply({ ...working, marks: { ...working.marks, color: pill } })
              }
              onClear={() => apply({ ...working, marks: { ...working.marks, color: null } })}
            />
            <EncodingSlot
              name="Size"
              pill={working.marks.size ?? null}
              fields={fields.filter((field) => !usedFields.has(field.field) || field.aggregation)}
              onPick={(pill) =>
                apply({ ...working, marks: { ...working.marks, size: pill } })
              }
              onClear={() => apply({ ...working, marks: { ...working.marks, size: null } })}
            />
          </div>
        </div>

        <div
          className="relative min-h-64 rounded-xl border bg-card p-4"
          aria-label="Viz canvas"
          aria-busy={compute.isPending}
        >
          {compute.isPending ? (
            <div className="absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-card/70">
              <Loader2 aria-hidden="true" className="size-5 animate-spin text-muted-foreground" />
              <span className="sr-only">Recomputing the view</span>
            </div>
          ) : null}
          <VizView presmodel={presmodel} />
        </div>

        <div className="flex items-center justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setWorking(spec);
              setPresmodel(initialPresmodel);
            }}
            disabled={compute.isPending || save.isPending}
          >
            Reset
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => save.mutate(working)}
            disabled={compute.isPending || save.isPending}
          >
            {save.isPending ? (
              <Loader2 aria-hidden="true" className="size-4 animate-spin" />
            ) : null}
            {savingLabel}
          </Button>
        </div>
        <p className="sr-only" role="status">
          {title} worksheet editor
        </p>
      </div>
    </div>
  );
}

/** Tableau's field pill: blue for discrete headers, green for continuous axes. */
export function Pill({ pill }: { pill: FieldRef }) {
  const continuous = pill.aggregation !== null && pill.aggregation !== undefined;
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1 truncate rounded-full px-2.5 py-0.5 text-xs font-medium",
        continuous
          ? "bg-[var(--pill-continuous)] text-[var(--pill-continuous-foreground)]"
          : "bg-[var(--pill-discrete)] text-[var(--pill-discrete-foreground)]",
      )}
    >
      {continuous
        ? `${pill.aggregation}(${pill.field})`
        : pill.field}
    </span>
  );
}

function Shelf({
  label,
  pills,
  onRemove,
  onCycle,
  className,
}: {
  label: string;
  pills: FieldRef[];
  onRemove: (pill: FieldRef) => void;
  onCycle: (pill: FieldRef) => void;
  className?: string;
}) {
  return (
    <div className={cn("flex min-h-9 items-center gap-2", className)}>
      <span className="w-16 shrink-0 text-xs font-medium text-muted-foreground">
        {label}
      </span>
      <div
        className="flex min-h-9 flex-1 flex-wrap items-center gap-1.5 rounded-lg border border-dashed px-2 py-1"
        aria-label={`${label} shelf`}
      >
        {pills.length ? (
          pills.map((pill, index) => (
            <span key={`${pill.field}-${index}`} className="inline-flex">
              <button
                type="button"
                onClick={() => onCycle(pill)}
                title="Switch between measures and dimensions"
                className="rounded-full focus-visible:outline-2 focus-visible:outline-ring"
              >
                <Pill pill={pill} />
              </button>
              <button
                type="button"
                onClick={() => onRemove(pill)}
                aria-label={`Remove ${pill.field} from ${label}`}
                className="ml-0.5 rounded-full p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
              >
                <X aria-hidden="true" className="size-3" />
              </button>
            </span>
          ))
        ) : (
          <span className="px-1 text-xs text-muted-foreground">Drop fields here</span>
        )}
      </div>
    </div>
  );
}

function EncodingSlot({
  name,
  pill,
  fields,
  onPick,
  onClear,
  unusedOnly = false,
}: {
  name: string;
  pill: FieldRef | null;
  fields: FieldRef[];
  onPick: (pill: FieldRef) => void;
  onClear: () => void;
  unusedOnly?: boolean;
}) {
  const options = unusedOnly
    ? fields.filter(
        (field) =>
          !field.aggregation ||
          field.field === pill?.field,
      )
    : fields;
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      <span className="text-muted-foreground">{name}</span>
      {pill ? (
        <>
          <Pill pill={pill} />
          <button
            type="button"
            onClick={onClear}
            aria-label={`Clear ${name} encoding`}
            className="rounded-full p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
          >
            <X aria-hidden="true" className="size-3" />
          </button>
        </>
      ) : (
        <Select
          value=""
          onValueChange={(value) => {
            const field = fields.find((item) => item.field === value);
            if (field) onPick(field);
          }}
        >
          <SelectTrigger
            aria-label={`Add ${name} encoding`}
            className="h-7 w-28 border-dashed bg-transparent text-xs text-muted-foreground"
          >
            <SelectValue placeholder="Add" />
          </SelectTrigger>
          <SelectContent>
            {options.map((field) => (
              <SelectItem key={field.field} value={field.field}>
                {field.aggregation ? `${field.aggregation}(${field.field})` : field.field}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </span>
  );
}

function classify(columns: string[], rows: (string | number | null)[][]): FieldRef[] {
  return columns.map((column, index) => {
    const values = rows
      .slice(0, 50)
      .map((row) => row[index])
      .filter((value) => value !== null && value !== undefined && value !== "");
    const numeric = values.length && values.every((value) => typeof value === "number" || (!Number.isNaN(Number(value)) && String(value).trim() !== ""));
    const temporal = !numeric && values.some((value) => /^\d{4}-\d{2}(-\d{2})?/.test(String(value)));
    return numeric
      ? { field: column, field_type: "quantitative" as const, aggregation: "sum" as const, discrete: false }
      : {
          field: column,
          field_type: temporal ? ("temporal" as const) : ("nominal" as const),
          discrete: true,
        };
  });
}

function addField(
  working: WorksheetSpec,
  field: FieldRef,
  apply: (next: WorksheetSpec) => void,
) {
  if (field.aggregation) {
    apply({ ...working, rows: [...working.rows, field] });
  } else {
    apply({ ...working, columns: [...working.columns, field] });
  }
}

function removePill(
  working: WorksheetSpec,
  shelf: "columns" | "rows",
  pill: FieldRef,
): WorksheetSpec {
  const pills = working[shelf].filter((item) => item.field !== pill.field);
  return { ...working, [shelf]: pills };
}

function cyclePill(
  working: WorksheetSpec,
  shelf: "columns" | "rows",
  pill: FieldRef,
): WorksheetSpec {
  const pills = working[shelf].map((item) => {
    if (item !== pill) return item;
    if (item.aggregation) {
      return { field: item.field, field_type: item.field_type === "quantitative" ? "nominal" : item.field_type, discrete: true };
    }
    return {
      field: item.field,
      field_type: "quantitative",
      aggregation: "sum" as Aggregation,
      discrete: false,
    };
  });
  return { ...working, [shelf]: pills };
}

function applyShowMe(
  working: WorksheetSpec,
  chartId: string,
  fields: FieldRef[],
  apply: (next: WorksheetSpec) => void,
) {
  const dims = fields.filter((field) => !field.aggregation);
  const dates = dims.filter((field) => field.field_type === "temporal");
  const measures = fields.filter((field) => field.aggregation);
  const axis = dates[0] ?? dims[0];
  const colorDim = dims.find((dimension) => dimension !== axis);

  const next: WorksheetSpec = {
    ...working,
    columns: axis ? [axis] : [],
    rows: measures.slice(0, chartId === "bar" && measures.length > 1 ? 2 : 1),
    marks: { type: "bar", color: null, size: null, text: null, detail: null, angle: null },
  };
  if (chartId === "line" || chartId === "area") {
    next.marks = { type: chartId, color: colorDim ?? null, size: null, text: null, detail: null, angle: null };
    next.rows = measures.slice(0, 1);
  } else if (chartId === "pie") {
    next.marks = {
      type: "pie",
      color: axis ?? null,
      size: null,
      text: null,
      detail: null,
      angle: measures[0] ?? null,
    };
  } else if (chartId === "scatter") {
    next.columns = measures.slice(0, 1);
    next.rows = measures.slice(1, 2).length ? measures.slice(1, 2) : measures.slice(0, 1);
    next.marks = { type: "circle", color: null, size: null, text: null, detail: dims[0] ?? null, angle: null };
  } else if (chartId === "text_table" || chartId === "heatmap") {
    next.marks = {
      type: "text",
      color: chartId === "heatmap" ? measures[0] ?? null : null,
      text: measures[0] ?? null,
      size: null,
      detail: null,
      angle: null,
    };
    next.columns = dims.slice(0, 1);
    next.rows = [...dims.slice(1, 2), ...measures.slice(0, 1)];
  } else if (chartId === "bar_stacked" || chartId === "bar_side") {
    next.marks = {
      type: "bar",
      color: colorDim ?? null,
      size: null,
      text: null,
      detail: null,
      angle: null,
    };
  }
  apply(next);
}
