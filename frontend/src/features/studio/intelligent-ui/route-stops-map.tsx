import { useContext, useEffect, useMemo, useState } from "react";
import { useStateField } from "@openuidev/react-lang";
import {
  MapMarker,
  MapPopup,
  MapRoute,
  MarkerContent,
} from "@/components/ui/map";
import { mapColor } from "@/components/ui/map/tokens";
import { cn } from "@/lib/utils";
import { NodeArtifactButton } from "./artifact-button";
import { geoBounds } from "./geometry";
import type { MapOptions } from "./map-options";
import { MapSurface } from "./map-surface";
import { caption, routeGroups, type RouteGroup } from "./route-data";
import { roadRoute, type RoadRoute } from "./route-road";
import { UiRuntimeContext } from "./runtime-context";

/** Numbered markers stay readable up to this many stops; beyond it they are dots. */
const NUMBERED_STOPS = 80;
/** Stops listed under the open route before the rest are summarised. */
const LISTED_STOPS = 12;

const routeToken = (index: number) => `--chart-${(index % 8) + 1}`;

function cell(value: unknown): string {
  if (typeof value === "number")
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  const numeric =
    typeof value === "string" && value.trim() !== ""
      ? Number(value)
      : Number.NaN;
  if (Number.isFinite(numeric))
    return numeric.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return value === null || value === undefined || value === ""
    ? "—"
    : String(value);
}

function boundsOf(route: RouteGroup) {
  return geoBounds(
    route.coordinates.map((coordinates, index) => ({
      type: "Feature" as const,
      id: index,
      geometry: { type: "Point" as const, coordinates },
      properties: {},
    })),
  );
}

/**
 * Delivery routes from ordered stop rows: one coloured line per route, numbered
 * stops in visit order, and the routes listed beside the map. Lines join stops in
 * order; they are not road paths, and the panel says so.
 */
export default function RouteStopsMap({
  p,
  rows,
  options,
}: {
  p: Record<string, unknown>;
  rows: Record<string, unknown>[];
  options: MapOptions & { sequence: string };
}) {
  const fields = useMemo(
    () => ({
      longitude: p.longitude as string | undefined,
      latitude: p.latitude as string | undefined,
      label: p.label as string | undefined,
    }),
    [p.longitude, p.latitude, p.label],
  );
  const { routes, invalidRows } = useMemo(
    () => routeGroups(rows, fields, options.sequence, options.group),
    [rows, fields, options.sequence, options.group],
  );
  const colors = useMemo(
    () => routes.map((_, index) => mapColor(routeToken(index))),
    [routes],
  );
  const bounds = useMemo(
    () =>
      geoBounds(
        routes.flatMap((route) =>
          route.coordinates.map((coordinates, index) => ({
            type: "Feature" as const,
            id: index,
            geometry: { type: "Point" as const, coordinates },
            properties: {},
          })),
        ),
      ),
    [routes],
  );
  const selected = useStateField("selected", p.selected);
  const viewport = useStateField("viewport", p.viewport);
  const { readOnly } = useContext(UiRuntimeContext);
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [popup, setPopup] = useState<number | null>(null);
  const [focusBounds, setFocusBounds] = useState<typeof bounds>(null);
  const [openRoute, setOpenRoute] = useState<string | null>(null);
  // Road lines arrive per route from the public routing service; until then (or
  // when it cannot answer) a route joins its stops in order.
  const [roads, setRoads] = useState<Record<string, RoadRoute>>({});
  useEffect(() => {
    let current = true;
    setRoads({});
    for (const route of routes) {
      void roadRoute(route.coordinates).then((road) => {
        if (current && road)
          setRoads((known) => ({ ...known, [route.key]: road }));
      });
    }
    return () => {
      current = false;
    };
  }, [routes]);
  const roadCount = routes.filter((route) => roads[route.key]).length;

  const selectedRow = typeof selected.value === "number" ? selected.value : -1;
  const activeKey =
    openRoute ??
    routes.find((route) => route.stops.some((stop) => stop.row === selectedRow))
      ?.key ??
    null;
  const stopCount = routes.reduce(
    (total, route) => total + route.stops.length,
    0,
  );
  const numbered = stopCount <= NUMBERED_STOPS;
  const popupStop = routes
    .flatMap((route) => route.stops)
    .find((stop) => stop.row === popup);
  const title = String(p.title ?? "Routes");

  const chooseStop = (row: number) => {
    if (readOnly) return;
    const route = routes.find((candidate) =>
      candidate.stops.some((stop) => stop.row === row),
    );
    const stop = route?.stops.find((candidate) => candidate.row === row);
    if (!route || !stop) return;
    selected.setValue(row);
    setOpenRoute(route.key);
    setPopup(row);
    setFocusBounds([stop.coordinates, stop.coordinates]);
  };
  const chooseRoute = (route: RouteGroup) => {
    setOpenRoute(route.key);
    setPopup(null);
    setFocusBounds(boundsOf(route));
    if (!readOnly && route.stops[0]) selected.setValue(route.stops[0].row);
  };

  // The routes beside the map: one framed list as tall as the map, each route a
  // row whose figures read label left, value right.
  const routeList = (
    <nav
      aria-label="Routes"
      className="min-w-0 max-h-80 overflow-y-auto rounded-lg border border-border p-1 sm:h-80 sm:max-h-none"
    >
      <ol className="space-y-1">
        {routes.map((route, index) => {
          const open = activeKey === route.key;
          const first = rows[route.stops[0]?.row ?? -1] ?? {};
          const figures: [string, unknown][] = [
            ...(options.routeFields ?? []).map(
              (field) => [caption(field), first[field]] as [string, unknown],
            ),
            ...(roads[route.key]
              ? [["By road (km)", roads[route.key].km] as [string, unknown]]
              : []),
          ];
          return (
            <li key={route.key}>
              <button
                type="button"
                aria-pressed={open}
                onClick={() => chooseRoute(route)}
                className={cn(
                  "w-full rounded-sm px-3 py-2 text-left transition-colors focus-visible:outline-2 focus-visible:outline-ring",
                  open ? "bg-muted" : "hover:bg-muted/60",
                )}
              >
                <span className="flex items-center gap-2 text-sm font-medium">
                  <span
                    aria-hidden
                    className="h-1 w-4 shrink-0 rounded-full"
                    style={{ background: `var(${routeToken(index)})` }}
                  />
                  <span className="min-w-0 flex-1 truncate">{route.key}</span>
                  <span className="text-xs font-normal text-muted-foreground tabular-nums">
                    {route.stops.length}{" "}
                    {route.stops.length === 1 ? "stop" : "stops"}
                  </span>
                </span>
                {figures.length > 0 && (
                  <dl className="mt-2 space-y-1 pl-6 text-xs">
                    {figures.map(([label, value]) => (
                      <div key={label} className="flex items-baseline gap-3">
                        <dt className="min-w-0 flex-1 truncate text-muted-foreground">
                          {label}
                        </dt>
                        <dd className="shrink-0 text-right tabular-nums">
                          {cell(value)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
              </button>
              {open && (
                <ol className="mt-1 space-y-0.5 pb-1 pl-3 text-xs">
                  {route.stops.slice(0, LISTED_STOPS).map((stop, position) => (
                    <li key={stop.row}>
                      <button
                        type="button"
                        onClick={() => chooseStop(stop.row)}
                        className={cn(
                          "flex w-full gap-2 rounded-sm px-1 py-0.5 text-left transition-colors hover:bg-muted/60 focus-visible:outline-2 focus-visible:outline-ring",
                          stop.row === selectedRow && "bg-muted",
                        )}
                      >
                        <span className="w-4 shrink-0 text-right text-muted-foreground tabular-nums">
                          {position + 1}
                        </span>
                        <span className="min-w-0 truncate">{stop.label}</span>
                      </button>
                    </li>
                  ))}
                  {route.stops.length > LISTED_STOPS && (
                    <li className="pl-7 text-muted-foreground">
                      +{route.stops.length - LISTED_STOPS} more stops
                    </li>
                  )}
                </ol>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );

  return (
    <section className="min-w-0 space-y-2" aria-label={title}>
      <div className="flex min-h-7 items-center gap-2">
        <p className="min-w-0 flex-1 text-sm font-medium">{title}</p>
        <NodeArtifactButton
          data={p.data}
          noun="map"
          draft={{ kind: "table", title }}
        />
      </div>
      <MapSurface
        aside={routeList}
        bounds={bounds}
        focusBounds={focusBounds}
        options={options}
        viewport={viewport.value}
        onViewport={(view) => {
          if (!readOnly && viewport.isReactive) viewport.setValue(view);
        }}
        onReady={() => setLoaded(true)}
        onError={() => setError(true)}
      >
        {routes.map((route, index) => (
          <MapRoute
            key={route.key}
            id={`route-${index}`}
            coordinates={roads[route.key]?.coordinates ?? route.coordinates}
            color={colors[index]}
            width={3}
            opacity={activeKey && activeKey !== route.key ? 0.35 : 0.9}
            active={activeKey === route.key}
            activeWidth={5}
            interactive={!readOnly}
            onClick={() => chooseRoute(route)}
          />
        ))}
        {routes.map((route, index) =>
          route.stops.map((stop, position) => (
            <MapMarker
              key={`${route.key}-${stop.row}`}
              longitude={stop.coordinates[0]}
              latitude={stop.coordinates[1]}
              onClick={() => chooseStop(stop.row)}
            >
              <MarkerContent>
                <span
                  aria-label={`${route.key}, stop ${position + 1}: ${stop.label}`}
                  className={cn(
                    "flex items-center justify-center rounded-full border-2 border-card font-semibold text-[10px] leading-none text-white tabular-nums shadow-sm",
                    numbered ? "size-5" : "size-2.5",
                    position === 0 && "ring-2 ring-foreground/70",
                    stop.row === selectedRow && "ring-2 ring-foreground",
                  )}
                  style={{ background: `var(${routeToken(index)})` }}
                >
                  {numbered ? position + 1 : null}
                </span>
              </MarkerContent>
            </MapMarker>
          )),
        )}
        {popupStop && (
          <MapPopup
            longitude={popupStop.coordinates[0]}
            latitude={popupStop.coordinates[1]}
            closeButton
            closeOnClick={false}
            onClose={() => setPopup(null)}
          >
            <p className="pr-10 text-sm font-medium">{popupStop.label}</p>
            <dl className="mt-2 space-y-1 text-xs">
              {(options.popupFields ?? []).map((field) => (
                <div key={field}>
                  <dt className="text-muted-foreground">{caption(field)}</dt>
                  <dd className="break-words">
                    {cell(rows[popupStop.row][field])}
                  </dd>
                </div>
              ))}
            </dl>
          </MapPopup>
        )}
      </MapSurface>
      <p role="status" className="text-xs text-muted-foreground">
        {error
          ? "Map unavailable; the routes stay listed. "
          : !loaded
            ? "Loading map. "
            : ""}
        {routes.length} {routes.length === 1 ? "route" : "routes"}, {stopCount}{" "}
        stops in visit order
        {invalidRows.length
          ? `; ${invalidRows.length} without coordinates or order`
          : ""}
        .{" "}
        {roadCount === routes.length && routes.length
          ? "Lines follow roads from OpenStreetMap, routed by the public OSRM service."
          : roadCount
            ? `${roadCount} of ${routes.length} routes follow roads (public OSRM); the rest join stops in order.`
            : "Lines join stops in order; road routing (public OSRM) is unavailable."}
      </p>
    </section>
  );
}
