import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
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
 * DashboardCanvas shows a dashboard saved before BI, read-only: a tiled zone
 * grid over chart artifacts. Quick filters and mark clicks re-compile every
 * affected sheet through the viz core, so it is live data under the viewer's
 * access. New dashboards are built in BI; this view lasts until the dashboard
 * is converted.
 */

type MarkSelection = Record<string, string | number | null> | null;

export function DashboardCanvas({
  spec,
  charts,
}: {
  spec: DashboardSpec;
  charts: StudioArtifact[];
}) {
  const [filterValues, setFilterValues] = useState<Record<string, Filter>>({});
  const [zoneFilters, setZoneFilters] = useState<Record<string, Filter[]>>({});
  const [selection, setSelection] = useState<MarkSelection>(null);
  const [result, setResult] = useState<DashboardComputeResponse | null>(null);

  const compute = useMutation({
    mutationFn: () =>
      studioApi.computeVizDashboard({
        spec: spec as unknown as Record<string, unknown>,
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

  useEffect(() => {
    compute.mutate();
  }, [spec, filterValues, zoneFilters]);

  const sheetResults = useMemo(() => {
    const byZone = new Map<string, DashboardComputeResponse["zones"][number]>();
    for (const zone of result?.zones ?? []) byZone.set(zone.zone_id, zone);
    return byZone;
  }, [result]);

  const rows = Math.max(1, ...spec.zones.map((zone) => zone.rect.y + zone.rect.h));
  const filterZones = spec.zones.filter((zone) => zone.kind === "filter");

  const applySelection = useCallback(
    (sourceZoneId: string, values: Record<string, string | number | null>) => {
      setSelection(values);
      const next: Record<string, Filter[]> = {};
      for (const zone of sheetZones(spec)) {
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
    [spec],
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
        {spec.zones.map((zone) => (
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
          </div>
        ))}
      </div>

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
}: {
  zone: Zone;
  title: string;
  result: { presmodel?: DashboardComputeResponse["zones"][number]["presmodel"]; error?: string } | undefined;
  pending: boolean;
  onMarkClick: (values: Record<string, string | number | null>) => void;
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
