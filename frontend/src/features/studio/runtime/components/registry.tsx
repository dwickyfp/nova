/**
 * Studio component registry: renders the components an answer is composed of.
 *
 * The props of each component are checked against the same Zod schemas the
 * server validated them with (`schemas.ts`). A component this client does not
 * know, or props that fail its schema, show a short notice instead of a guess.
 */
import { lazy, Suspense, useMemo, type ReactNode } from "react";
import { NativeChart } from "@/features/native-dataviz/charts/native-chart";
import { chartSchema, type ChartDefinition } from "@/features/native-dataviz/contracts";
import { AddToDatavizButton } from "../../dataviz/add-to-dataviz";
import type { DatavizLocator } from "../../dataviz/api";
import { MapFieldContext, useLocalMapField } from "../../intelligent-ui/map-state";
import { UiRuntimeContext } from "../../intelligent-ui/runtime-context";
import type { ComponentBlock } from "./block";
import { FigureTile } from "./figure-tile";
import type { DatavizChartProps, GeoMapProps, KpiTilesProps } from "./schemas";

const GeoMapView = lazy(() => import("../../intelligent-ui/geo-map"));

export function ComponentView({
  block,
  ready,
  dataviz,
}: {
  block: ComponentBlock;
  /** The turn is stored: a draft chart's query can be read back from it. */
  ready: boolean;
  /** Where the component is stored, for Add to Dataviz. */
  dataviz?: DatavizLocator | null;
}) {
  switch (block.component) {
    case "DatavizChart":
      return <DatavizChart props={block.props} ready={ready} dataviz={dataviz ?? null} />;
    case "KpiTiles":
      return <KpiTiles props={block.props} />;
    case "GeoMap":
      return <GeoMap props={block.props} />;
  }
}

export function DatavizChart({
  props,
  ready,
  dataviz = null,
  action,
}: {
  props: DatavizChartProps;
  ready: boolean;
  dataviz?: DatavizLocator | null;
  /** The header action in place of Add to Dataviz by locator (a program's node). */
  action?: ReactNode;
}) {
  const chart = useMemo(() => {
    const parsed = chartSchema.safeParse(props.chart);
    return parsed.success ? (parsed.data as ChartDefinition) : null;
  }, [props.chart]);
  return (
    <section
      className="nova-chat-item min-w-0 rounded-xl border bg-card p-4"
      aria-label={props.title}
      data-component="DatavizChart"
    >
      <div className="mb-2 flex min-h-7 items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate text-sm font-medium">{props.title}</h3>
        {action ?? <AddToDatavizButton locator={ready ? dataviz : null} />}
      </div>
      <div className="flex h-72 min-w-0 flex-col">
        {!chart ? (
          <p className="m-auto text-sm text-muted-foreground">This chart cannot be shown.</p>
        ) : ready ? (
          <NativeChart chart={chart} draft draftRef={props.draft_ref} />
        ) : (
          // The chart's query is read from the stored turn; until then hold its box.
          <div
            role="status"
            aria-label={`Preparing ${props.title}`}
            className="min-h-0 flex-1 rounded-lg bg-muted/60 motion-safe:animate-pulse"
          />
        )}
      </div>
    </section>
  );
}

export function KpiTiles({ props }: { props: KpiTilesProps }) {
  return (
    <section className="nova-chat-item min-w-0" aria-label={props.title || "Key figures"} data-component="KpiTiles">
      {props.title ? <h3 className="mb-2 text-sm font-medium">{props.title}</h3> : null}
      <dl className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2 lg:grid-cols-3">
        {props.items.map((item) => (
          <FigureTile key={`${item.evidence_id}:${item.column}`} figure={item} />
        ))}
      </dl>
    </section>
  );
}

const MAP_RUNTIME = { readOnly: false };

export function GeoMap({ props }: { props: GeoMapProps }) {
  const p = useMemo(
    () => ({
      title: props.title,
      data: props.rows,
      longitude: props.longitude,
      latitude: props.latitude,
      geometry: props.geometry,
      label: props.label,
      options: props.options,
    }),
    [props],
  );
  return (
    <div className="nova-chat-item min-w-0" data-component="GeoMap">
      <UiRuntimeContext.Provider value={MAP_RUNTIME}>
        <MapFieldContext.Provider value={useLocalMapField}>
          <Suspense fallback={<p role="status" className="text-sm text-muted-foreground">Loading map…</p>}>
            <GeoMapView p={p} />
          </Suspense>
        </MapFieldContext.Provider>
      </UiRuntimeContext.Provider>
    </div>
  );
}
