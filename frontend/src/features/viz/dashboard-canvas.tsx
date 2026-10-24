import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Loader2, PencilLine, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { studioApi, type StudioArtifact } from "@/features/agents/api";
import { cn } from "@/lib/utils";
import { describeView } from "./insight";
import { VizView } from "./viz-view";
import type { DashboardComputeResponse, DashboardSpec, Filter, Zone } from "./types";

/**
 * DashboardCanvas is the Studio dashboard on Nova tokens: a tiled zone grid
 * over chart artifacts. Quick filters and mark clicks re-compile every
 * affected sheet through the viz core, so a dashboard is live data, not a
 * screenshot.
 */

type MarkSelection = Record<string, string | number | null> | null;

export function DashboardCanvas({
  artifactId,
  spec,
  charts,
  expectedUpdatedAt,
  editable = false,
  onSaved,
  onEditSheet,
}: {
  artifactId: string;
  spec: DashboardSpec;
  charts: StudioArtifact[];
  expectedUpdatedAt: string;
  editable?: boolean;
  onSaved?: (spec: DashboardSpec) => void;
  /** Open a zone's sheet in the worksheet editor ("go to sheet"). */
  onEditSheet?: (artifactId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [working, setWorking] = useState<DashboardSpec>(spec);
  const [filterValues, setFilterValues] = useState<Record<string, Filter>>({});
  const [zoneFilters, setZoneFilters] = useState<Record<string, Filter[]>>({});
  const [selection, setSelection] = useState<MarkSelection>(null);
  const [result, setResult] = useState<DashboardComputeResponse | null>(null);

  useEffect(() => setWorking(spec), [spec]);

  const compute = useMutation({
    mutationFn: () =>
      studioApi.computeVizDashboard({
        spec: working as unknown as Record<string, unknown>,
        filter_values: Object.fromEntries(
          Object.entries(filterValues).map(([zoneId, filter]) => [
            zoneId,
            filter as unknown as Record<string, unknown>,
          ]),
        ),
        zone_filters: Object.fromEntries(
          Object.entries(zoneFilters).map(([zoneId, filters]) => [
            zoneId,
            filters.map((filter) => filter as unknown as Record<string, unknown>),
          ]),
        ),
      }),
    onSuccess: setResult,
    onError: (error: Error) => toast.error(error.message),
  });

  const save = useMutation({
    mutationFn: (next: DashboardSpec) =>
      studioApi.updateArtifactSpec(artifactId, {
        viz_spec: next as unknown as Record<string, unknown>,
        expected_updated_at: expectedUpdatedAt,
      }),
    onSuccess: () => {
      toast.success("Dashboard saved");
      void queryClient.invalidateQueries({ queryKey: ["studio", "artifacts"] });
      onSaved?.(working);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  useEffect(() => {
    compute.mutate();
  }, [working, filterValues, zoneFilters]);

  const sheetResults = useMemo(() => {
    const byZone = new Map<string, DashboardComputeResponse["zones"][number]>();
    for (const zone of result?.zones ?? []) byZone.set(zone.zone_id, zone);
    return byZone;
  }, [result]);

  const rows = Math.max(1, ...working.zones.map((zone) => zone.rect.y + zone.rect.h));
  const filterZones = working.zones.filter((zone) => zone.kind === "filter");

  const applySelection = useCallback(
    (sourceZoneId: string, values: Record<string, string | number | null>) => {
      setSelection(values);
      const next: Record<string, Filter[]> = {};
      for (const zone of sheetZones(working)) {
        if (zone.zone_id === sourceZoneId) continue;
        const filters: Filter[] = [];
        for (const [field, value] of Object.entries(values)) {
          if (value === null || value === undefined) continue;
          filters.push({ kind: "categorical", field, values: [String(value)] });
        }
        if (filters.length) next[zone.zone_id] = filters;
      }
      setZoneFilters(next);
    },
    [working],
  );

  return (
    <div className="flex min-w-0 flex-col gap-3">
      {filterZones.length || selection ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card px-4 py-2" role="toolbar" aria-label="Dashboard filters">
          <span className="type-meta">Applies to every sheet</span>
          {filterZones.map((zone) => (
            <DashboardFilter
              key={zone.zone_id}
              zone={zone}
              members={collectMembers(sheetResults, zone.filter?.field ?? "")}
              value={filterValues[zone.zone_id] ?? null}
              onChange={(filter) =>
                setFilterValues((current) => {
                  const next = { ...current };
                  if (filter) next[zone.zone_id] = filter;
                  else delete next[zone.zone_id];
                  return next;
                })
              }
            />
          ))}
          {selection ? (
            <span className="type-meta ml-auto inline-flex items-center gap-2">
              Filtering by {Object.entries(selection).map(([field, value]) => `${field}: ${value}`).join(", ")}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="type-meta h-6 px-2"
                onClick={() => {
                  setSelection(null);
                  setZoneFilters({});
                }}
              >
                Clear
              </Button>
            </span>
          ) : null}
        </div>
      ) : null}

      <div
        className="grid gap-3"
        style={{
          gridTemplateColumns: "repeat(24, minmax(0, 1fr))",
          gridTemplateRows: `repeat(${rows}, minmax(7rem, auto))`,
        }}
        aria-label="Dashboard canvas"
      >
        {working.zones.map((zone) => (
          <div
            key={zone.zone_id}
            className={cn(
              "relative min-w-0 rounded-lg border bg-card p-4",
              zoneFilters[zone.zone_id] && "border-ring",
            )}
            style={{
              gridColumn: `${zone.rect.x + 1} / span ${zone.rect.w}`,
              gridRow: `${zone.rect.y + 1} / span ${zone.rect.h}`,
            }}
          >
            {zone.kind === "sheet" ? (
              <SheetZone
                zone={zone}
                title={
                  sheetResults.get(zone.zone_id)?.title ??
                  charts.find((chart) => chart.artifact_id === zone.artifact_id)?.title ??
                  "Chart"
                }
                result={sheetResults.get(zone.zone_id)}
                pending={compute.isPending}
                onMarkClick={(values) => applySelection(zone.zone_id, values)}
                onEdit={
                  onEditSheet && zone.artifact_id
                    ? () => onEditSheet(zone.artifact_id as string)
                    : undefined
                }
              />
            ) : zone.kind === "text" ? (
              zone.text?.style === "title" ? (
                <h2 className="type-answer">{zone.text.content}</h2>
              ) : (
                <p className="type-body">{zone.text?.content}</p>
              )
            ) : zone.kind === "filter" ? (
              <p className="type-meta">
                Filter: <span className="type-code">{zone.filter?.field}</span>
              </p>
            ) : (
              <div className="h-full w-full" aria-label="Spacer" />
            )}
            {editable ? (
              <ZoneControls
                onDelete={() =>
                  setWorking({ ...working, zones: working.zones.filter((item) => item.zone_id !== zone.zone_id) })
                }
                onMove={(direction) => setWorking(moveZone(working, zone, direction))}
                onResize={(direction) => setWorking(resizeZone(working, zone, direction))}
              />
            ) : null}
          </div>
        ))}
      </div>

      {editable ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border bg-card px-3 py-2">
          <AddChartPicker
            charts={charts}
            onAdd={(artifactId) =>
              setWorking((current) => addZone(current, artifactId))
            }
          />
          <Button
            type="button"
            size="sm"
            onClick={() => save.mutate(working)}
            disabled={save.isPending || !working.zones.length}
          >
            {save.isPending ? (
              <Loader2 aria-hidden="true" className="size-4 animate-spin" />
            ) : null}
            Save dashboard
          </Button>
        </div>
      ) : null}
      <p className="sr-only" role="status">
        {compute.isPending ? "Recomputing dashboard sheets." : ""}
      </p>
    </div>
  );
}

function SheetZone({
  zone,
  title,
  result,
  pending,
  onMarkClick,
  onEdit,
}: {
  zone: Zone;
  title: string;
  result: { presmodel?: DashboardComputeResponse["zones"][number]["presmodel"]; error?: string } | undefined;
  pending: boolean;
  onMarkClick: (values: Record<string, string | number | null>) => void;
  onEdit?: () => void;
}) {
  const insight = result?.presmodel ? describeView(result.presmodel) : null;
  return (
    <div className="viz-numerals flex h-full min-w-0 flex-col gap-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="type-question truncate">{title}</h3>
          {insight ? (
            <p className="type-title mt-1 text-foreground">
              <span className={insight.lead.marked ? "text-viz-highlight" : undefined}>{insight.lead.subject}</span> {insight.lead.verb} {insight.lead.num}
              {insight.lead.unit ? <span className="type-unit">{insight.lead.unit}</span> : null}
            </p>
          ) : null}
        </div>
        {onEdit ? (
          <button
            type="button"
            onClick={onEdit}
            aria-label={`Edit sheet ${title}`}
            className="type-meta inline-flex h-7 shrink-0 items-center gap-1 rounded-md border px-2 text-ink-secondary hover:bg-surface-1 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
          >
            <PencilLine aria-hidden="true" className="size-3.5" />
            Edit sheet
          </button>
        ) : null}
      </div>
      <div className="relative min-w-0 flex-1">
        {pending ? (
          <div className="absolute inset-0 z-10 flex items-center justify-center">
            <Loader2 aria-hidden="true" className="size-4 animate-spin text-muted-foreground" />
          </div>
        ) : null}
        {result?.error ? (
          <p className="type-meta py-6 text-center text-destructive">{result.error}</p>
        ) : result?.presmodel ? (
          <VizView presmodel={result.presmodel} compact onMarkClick={onMarkClick} highlight={insight?.highlight ?? null} />
        ) : !pending ? (
          <p className="type-meta py-6 text-center">This sheet returned no rows under your access.</p>
        ) : null}
      </div>
      <span className="sr-only">{`Sheet zone ${zone.zone_id}`}</span>
    </div>
  );
}

function DashboardFilter({
  zone,
  members,
  value,
  onChange,
}: {
  zone: Zone;
  members: string[];
  value: Filter | null;
  onChange: (filter: Filter | null) => void;
}) {
  const field = zone.filter?.field ?? "";
  return (
    <label className="inline-flex min-h-9 items-center gap-2">
      <span className="type-code text-ink-secondary">{field}</span>
      <Select
        value={value ? String((value as { values: string[] }).values?.[0] ?? "") : "all"}
        onValueChange={(selected) => {
          onChange(
            selected === "all"
              ? null
              : ({ kind: "categorical", field, values: [selected] } as Filter),
          );
        }}
      >
        <SelectTrigger aria-label={`Filter by ${field}`} className="type-ui h-8 w-44 bg-background">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All</SelectItem>
          {members.map((member) => (
            <SelectItem key={member} value={member}>
              {member}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );
}

function AddChartPicker({
  charts,
  onAdd,
}: {
  charts: StudioArtifact[];
  onAdd: (artifactId: string) => void;
}) {
  const unused = charts.filter((chart) => chart.artifact_type === "chart");
  return (
    <span className="inline-flex items-center gap-2 text-xs">
      <span className="text-muted-foreground">Add chart</span>
      <Select
        value=""
        onValueChange={(value) => {
          if (value) onAdd(value);
        }}
      >
        <SelectTrigger aria-label="Add a chart to the dashboard" className="h-8 w-56 bg-background text-xs">
          <SelectValue placeholder="Choose a chart" />
        </SelectTrigger>
        <SelectContent>
          {unused.length ? (
            unused.map((chart) => (
              <SelectItem key={chart.artifact_id} value={chart.artifact_id}>
                {chart.title}
              </SelectItem>
            ))
          ) : (
            <SelectItem value="none" disabled>
              No charts available
            </SelectItem>
          )}
        </SelectContent>
      </Select>
    </span>
  );
}

function ZoneControls({
  onDelete,
  onMove,
  onResize,
}: {
  onDelete: () => void;
  onMove: (direction: "left" | "right" | "up" | "down") => void;
  onResize: (direction: "wider" | "narrower" | "taller" | "shorter") => void;
}) {
  const control =
    "flex size-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring";
  return (
    <div className="absolute right-1.5 top-1.5 flex items-center gap-0.5 rounded-lg border bg-card/95 p-0.5 shadow-sm">
      <button type="button" className={control} onClick={() => onMove("left")} aria-label="Move zone left">
        <ArrowLeft aria-hidden="true" className="size-3" />
      </button>
      <button type="button" className={control} onClick={() => onMove("right")} aria-label="Move zone right">
        <ArrowRight aria-hidden="true" className="size-3" />
      </button>
      <button type="button" className={control} onClick={() => onMove("up")} aria-label="Move zone up">
        <ArrowUp aria-hidden="true" className="size-3" />
      </button>
      <button type="button" className={control} onClick={() => onMove("down")} aria-label="Move zone down">
        <ArrowDown aria-hidden="true" className="size-3" />
      </button>
      <span className="mx-0.5 h-4 w-px bg-border" aria-hidden="true" />
      <button type="button" className={control} onClick={() => onResize("wider")} aria-label="Make zone wider">
        <Plus aria-hidden="true" className="size-3" />
      </button>
      <button type="button" className={control} onClick={() => onResize("narrower")} aria-label="Make zone narrower">
        <X aria-hidden="true" className="size-3" />
      </button>
      <button type="button" className={control} onClick={onDelete} aria-label="Remove zone">
        <Trash2 aria-hidden="true" className="size-3" />
      </button>
    </div>
  );
}

function sheetZones(spec: DashboardSpec): Zone[] {
  return spec.zones.filter((zone) => zone.kind === "sheet");
}

function collectMembers(
  results: Map<string, DashboardComputeResponse["zones"][number]>,
  field: string,
): string[] {
  const members = new Set<string>();
  for (const zone of results.values()) {
    if (!zone.presmodel) continue;
    zone.presmodel.columns.forEach((column, index) => {
      if (column.field !== field && column.name !== field) return;
      for (const row of zone.presmodel?.rows ?? []) {
        const value = row[index];
        if (value !== null && value !== undefined) members.add(String(value));
      }
    });
  }
  return [...members].sort().slice(0, 200);
}

function addZone(spec: DashboardSpec, artifactId: string): DashboardSpec {
  const zone: Zone = {
    zone_id: `zone_${Date.now().toString(36)}`,
    kind: "sheet",
    artifact_id: artifactId,
    rect: { x: 0, y: maxRow(spec), w: 12, h: 2 },
  };
  return { ...spec, zones: [...spec.zones, zone] };
}

function maxRow(spec: DashboardSpec): number {
  return spec.zones.reduce((maximum, zone) => Math.max(maximum, zone.rect.y + zone.rect.h), 0);
}

function moveZone(
  spec: DashboardSpec,
  zone: Zone,
  direction: "left" | "right" | "up" | "down",
): DashboardSpec {
  const step = { left: [-1, 0], right: [1, 0], up: [0, -1], down: [0, 1] }[direction];
  return {
    ...spec,
    zones: spec.zones.map((item) =>
      item.zone_id === zone.zone_id
        ? {
            ...item,
            rect: {
              ...item.rect,
              x: Math.max(0, Math.min(24 - item.rect.w, item.rect.x + step[0])),
              y: Math.max(0, item.rect.y + step[1]),
            },
          }
        : item,
    ),
  };
}

function resizeZone(
  spec: DashboardSpec,
  zone: Zone,
  direction: "wider" | "narrower" | "taller" | "shorter",
): DashboardSpec {
  return {
    ...spec,
    zones: spec.zones.map((item) => {
      if (item.zone_id !== zone.zone_id) return item;
      const rect = { ...item.rect };
      if (direction === "wider") rect.w = Math.min(24 - rect.x, rect.w + 2);
      if (direction === "narrower") rect.w = Math.max(3, rect.w - 2);
      if (direction === "taller") rect.h = Math.min(12, rect.h + 1);
      if (direction === "shorter") rect.h = Math.max(1, rect.h - 1);
      return { ...item, rect };
    }),
  };
}

