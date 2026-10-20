import { useContext, useEffect, useMemo, useState } from "react";
import { useStateField } from "@openuidev/react-lang";
import {
  MapMarker,
  MarkerContent,
  MarkerLabel,
  MarkerPopup,
  MarkerTooltip,
  MapRoute,
  RouteMarker,
  RouteProgress,
  MapArc,
} from "@/components/ui/map";
import { mapColor } from "@/components/ui/map/tokens";
import { Button } from "@/components/ui/button";
import { geoBounds, geoFeatures } from "./geometry";
import { MapSurface } from "./map-surface";
import { mapOptionsSchema } from "./map-options";
import { UiRuntimeContext } from "./runtime-context";

import { useMapMetrics } from "./map-metrics";

type Place = { latitude: number; longitude: number; label: string };
type Route = {
  geometry: GeoJSON.LineString;
  distance?: number;
  duration?: number;
};
export default function PublicMap({
  p,
  result,
}: {
  p: Record<string, unknown>;
  result: Record<string, unknown>;
}) {
  const metric = useMapMetrics();
  useEffect(() => {
    if (result.route_unavailable) metric("route_unavailable");
  }, [result.route_unavailable, metric]);
  const options = useMemo(
    () => mapOptionsSchema.parse(p.options ?? {}),
    [p.options],
  );
  const markers = (result.markers ?? []) as Place[];
  const [edits, setEdits] = useState<Record<number, Place>>({});
  const places = markers.map((place, i) => edits[i] ?? place);
  const [error, setError] = useState(false);
  const [progress, setProgress] = useState(0);
  const viewport = useStateField("viewport", p.viewport);
  const selection = useStateField("routeSelected", p.routeSelected);
  const { readOnly } = useContext(UiRuntimeContext);
  const routes = (
    (result.routes as Route[] | undefined) ??
    (result.geometry
      ? [{ geometry: result.geometry as GeoJSON.LineString }]
      : [])
  ).slice(0, 3);
  const features = geoFeatures(places, {
    longitude: "longitude",
    latitude: "latitude",
    label: "label",
  });
  const bounds = geoBounds(features.collection.features);
  const edited = Object.keys(edits).length > 0;
  const active =
    typeof selection.value === "number" && routes[selection.value]
      ? selection.value
      : 0;
  const primary = mapColor("--primary");
  return (
    <section
      className="min-w-0 space-y-2"
      aria-label={String(p.label ?? "Public map")}
    >
      <MapSurface
        publicMap
        bounds={bounds}
        options={options}
        viewport={viewport.value}
        onError={() => setError(true)}
        onViewport={(view) => {
          if (!readOnly && viewport.isReactive) viewport.setValue(view);
        }}
      >
        {places.map((place, index) => (
          <MapMarker
            key={index}
            longitude={place.longitude}
            latitude={place.latitude}
            draggable={!readOnly && options.editable}
            onDragEnd={({ lng, lat }) =>
              setEdits((prev) => ({
                ...prev,
                [index]: { ...place, longitude: lng, latitude: lat },
              }))
            }
          >
            <MarkerContent>
              <button
                type="button"
                aria-label={`Open ${place.label}`}
                className="grid min-h-11 min-w-11 place-items-center rounded-full border-2 border-primary bg-primary/25 text-sm font-semibold text-foreground focus-visible:outline-2 focus-visible:outline-ring"
              >
                {index + 1}
              </button>
              <MarkerLabel>{place.label}</MarkerLabel>
            </MarkerContent>
            <MarkerTooltip>{place.label}</MarkerTooltip>
            <MarkerPopup closeButton>
              <p className="pr-10 text-sm font-medium">{place.label}</p>
            </MarkerPopup>
          </MapMarker>
        ))}
        {!edited &&
          routes.map((route, i) => (
            <MapRoute
              key={i}
              coordinates={route.geometry.coordinates as [number, number][]}
              color={primary}
              opacity={i === active ? 1 : 0.35}
              active={i === active}
              onClick={() => {
                if (!readOnly) selection.setValue(i);
              }}
              interactive={!readOnly}
              progress={options.simulation ? progress : undefined}
            >
              {options.simulation && (
                <>
                  <RouteProgress width={7} />
                  <RouteMarker at="progress">
                    <MarkerContent />
                  </RouteMarker>
                </>
              )}
            </MapRoute>
          ))}
        {options.layer === "arc" && places.length > 1 && (
          <MapArc
            data={places
              .slice(1)
              .map((place, i) => ({
                id: String(i),
                from: [places[i].longitude, places[i].latitude] as [
                  number,
                  number,
                ],
                to: [place.longitude, place.latitude] as [number, number],
              }))}
            paint={{ "line-color": primary }}
          />
        )}
      </MapSurface>
      {error && (
        <p role="status" className="text-sm text-muted-foreground">
          The public map is unavailable. Places are listed below.
        </p>
      )}
      {result.route_unavailable === true && (
        <p role="status" className="text-sm text-muted-foreground">
          Driving route unavailable. Places remain listed.
        </p>
      )}
      {!edited && routes.length > 0 && (
        <div
          role="group"
          aria-label="Route options"
          className="flex flex-wrap gap-2"
        >
          {routes.map((route, i) => (
            <Button
              key={i}
              variant={i === active ? "secondary" : "ghost"}
              disabled={readOnly}
              aria-pressed={i === active}
              onClick={() => selection.setValue(i)}
            >
              Route {i + 1}
              {typeof route.distance === "number"
                ? ` · ${(route.distance / 1000).toFixed(1)} km`
                : ""}
              {typeof route.duration === "number"
                ? ` · about ${Math.round(route.duration / 60)} min`
                : ""}
            </Button>
          ))}
        </div>
      )}
      {options.simulation && !edited && routes.length > 0 && (
        <label className="block text-sm">
          Simulated route progress: {Math.round(progress * 100)}%
          <input
            aria-label="Simulated route progress"
            className="block w-full accent-primary"
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={progress}
            disabled={readOnly}
            onChange={(e) => setProgress(Number(e.target.value))}
          />
        </label>
      )}
      {options.layer === "arc" && (
        <p className="text-xs text-muted-foreground">
          Arcs show connections, not road routes or measured travel distances.
        </p>
      )}
      {edited && (
        <p role="status" className="text-xs text-muted-foreground">
          Edited locations are temporary assumptions. Original routes are
          hidden.
        </p>
      )}
      <ol className="list-inside list-decimal space-y-2 text-sm">
        {places.map((place, i) => (
          <li key={i}>
            {place.label}
            {options.editable && (
              <fieldset
                disabled={readOnly}
                className="mt-1 flex flex-wrap gap-2"
              >
                <legend className="text-xs text-muted-foreground">
                  Location assumption
                </legend>
                {(["longitude", "latitude"] as const).map((field) => (
                  <label key={field} className="text-xs">
                    {field}
                    <input
                      aria-label={`${place.label} ${field}`}
                      type="number"
                      step="0.0001"
                      min={field === "longitude" ? -180 : -90}
                      max={field === "longitude" ? 180 : 90}
                      className="ml-1 w-28 rounded-md border bg-background px-2 py-1"
                      value={place[field]}
                      onChange={(e) => {
                        const value = e.target.valueAsNumber;
                        if (
                          Number.isFinite(value) &&
                          Math.abs(value) <= (field === "longitude" ? 180 : 90)
                        )
                          setEdits((prev) => ({
                            ...prev,
                            [i]: { ...place, [field]: value },
                          }));
                      }}
                    />
                  </label>
                ))}
              </fieldset>
            )}
          </li>
        ))}
      </ol>
      {edited && (
        <Button variant="ghost" onClick={() => setEdits({})}>
          Restore original locations
        </Button>
      )}
    </section>
  );
}
