import { useContext, useMemo, useState } from "react";
import { useStateField } from "@openuidev/react-lang";
import { Button } from "@/components/ui/button";
import {
  MapArc,
  MapClusterLayer,
  MapGeoJSON,
  MapPopup,
  MapRoute,
  RouteMarker,
  RouteProgress,
  MarkerContent,
} from "@/components/ui/map";
import { mapColor } from "@/components/ui/map/tokens";
import { geoBounds } from "./geometry";
import { mapData } from "./map-data";
import { mapOptionsSchema } from "./map-options";
import { MAPCN_ENABLED } from "./map-flag";
import { MapSurface } from "./map-surface";
import { NodeArtifactButton } from "./artifact-button";
import { UiRuntimeContext } from "./runtime-context";
import LegacyGeoMap from "./geo-map-legacy";

export default function GeoMap({ p }: { p: Record<string, unknown> }) {
  if (!MAPCN_ENABLED) return <LegacyGeoMap p={p} />;
  return <MapcnGeoMap p={p} />;
}

function MapcnGeoMap({ p }: { p: Record<string, unknown> }) {
  const rows = useMemo(
    () => (Array.isArray(p.data) ? (p.data as Record<string, unknown>[]) : []),
    [p.data],
  );
  const fields = useMemo(
    () => ({
      longitude: p.longitude as string | undefined,
      latitude: p.latitude as string | undefined,
      geometry: p.geometry as string | undefined,
      label: p.label as string | undefined,
    }),
    [p.longitude, p.latitude, p.geometry, p.label],
  );
  const options = useMemo(
    () => mapOptionsSchema.parse(p.options ?? {}),
    [p.options],
  );
  const data = useMemo(
    () => mapData(rows, fields, options),
    [rows, fields, options],
  );
  const points = useMemo(
    () => ({
      ...data.collection,
      features: data.collection.features.filter(
        (f): f is GeoJSON.Feature<GeoJSON.Point> => f.geometry.type === "Point",
      ),
    }),
    [data],
  );
  const shapes = useMemo(
    () => ({
      ...data.collection,
      features: data.collection.features.filter(
        (f) => f.geometry.type !== "Point",
      ),
    }),
    [data],
  );
  const bounds = useMemo(() => geoBounds(data.collection.features), [data]);
  const selected = useStateField("selected", p.selected);
  const viewport = useStateField("viewport", p.viewport);
  const { readOnly } = useContext(UiRuntimeContext);
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [tableExpanded, setTableExpanded] = useState(false);
  const [popup, setPopup] = useState<number | null>(null);
  const [focusBounds, setFocusBounds] = useState<typeof bounds>(null);
  const selectedFeature = data.collection.features.find(
    (f) => f.id === selected.value,
  );
  const popupFeature = data.collection.features.find((f) => f.id === popup);
  const popupBounds = popupFeature ? geoBounds([popupFeature]) : null;
  const choose = (row: number) => {
    const feature = data.collection.features.find((f) => f.id === row);
    if (!feature || readOnly) return;
    selected.setValue(row);
    setPopup(row);
    setFocusBounds(geoBounds([feature]));
  };
  const primary = mapColor("--primary");
  const fail = () => {
    setError(true);
    setTableExpanded(true);
  };
  return (
    <section
      className="min-w-0 space-y-2"
      aria-label={String(p.title ?? "Business map")}
    >
      <div className="flex min-h-7 items-center gap-2">
        <p className="min-w-0 flex-1 text-sm font-medium">{String(p.title ?? "Business map")}</p>
        <NodeArtifactButton
          data={p.data}
          noun="map"
          draft={{ kind: "table", title: String(p.title ?? "Business map") }}
        />
      </div>
      <MapSurface
        bounds={bounds}
        focusBounds={focusBounds}
        options={options}
        viewport={viewport.value}
        onViewport={(view) => {
          if (!readOnly && viewport.isReactive) viewport.setValue(view);
        }}
        onReady={() => setLoaded(true)}
        onError={fail}
      >
        <MapClusterLayer
          data={points}
          onPointClick={(f) => choose(Number(f.properties?.row))}
        />
        {options.layer !== "route" && options.layer !== "arc" && (
          <MapGeoJSON
            data={shapes}
            promoteId="row"
            interactive={!readOnly}
            fillPaint={{
              "fill-color": primary,
              "fill-opacity": [
                "case",
                [
                  "==",
                  ["get", "row"],
                  typeof selected.value === "number" ? selected.value : -1,
                ],
                0.5,
                0.2,
              ],
            }}
            linePaint={{
              "line-color": primary,
              "line-width": [
                "case",
                [
                  "==",
                  ["get", "row"],
                  typeof selected.value === "number" ? selected.value : -1,
                ],
                4,
                2,
              ],
            }}
            onClick={(e) => choose(Number(e.feature.properties?.row))}
          />
        )}
        {options.layer === "route" &&
          shapes.features.map(
            (f) =>
              f.geometry.type === "LineString" && (
                <MapRoute
                  key={f.id}
                  coordinates={f.geometry.coordinates as [number, number][]}
                  color={primary}
                  active={selected.value === f.id}
                  progress={
                    options.progress
                      ? Number(rows[Number(f.id)][options.progress])
                      : undefined
                  }
                  onClick={() => choose(Number(f.id))}
                  interactive={!readOnly}
                >
                  <RouteProgress color={primary} width={6} />
                  <RouteMarker at="start">
                    <MarkerContent>
                      <span className="block size-3 rounded-full border-2 border-primary bg-primary/25" />
                    </MarkerContent>
                  </RouteMarker>
                  <RouteMarker at="end">
                    <MarkerContent>
                      <span className="block size-3 rounded-full border-2 border-primary bg-primary/25" />
                    </MarkerContent>
                  </RouteMarker>
                </MapRoute>
              ),
          )}
        {options.layer === "arc" && (
          <MapArc
            samples={Math.min(
              64,
              Math.floor(20000 / Math.max(1, shapes.features.length)),
            )}
            data={shapes.features
              .filter((f) => f.geometry.type === "LineString")
              .map((f) => ({
                id: String(f.id),
                from: f.geometry.coordinates[0] as [number, number],
                to: f.geometry.coordinates[1] as [number, number],
                row: f.id,
              }))}
            interactive={!readOnly}
            paint={{ "line-color": primary, "line-width": 3 }}
            onClick={(e) => choose(Number(e.arc.row))}
          />
        )}
        {popupFeature && popupBounds && (
          <MapPopup
            longitude={popupBounds[0][0]}
            latitude={popupBounds[0][1]}
            closeButton
            closeOnClick={false}
            onClose={() => setPopup(null)}
          >
            <p className="pr-10 text-sm font-medium">
              {String(popupFeature.properties?.label)}
            </p>
            <dl className="mt-2 space-y-1 text-xs">
              {(options.popupFields ?? []).map((field) => (
                <div key={field}>
                  <dt className="text-muted-foreground">{field}</dt>
                  <dd className="break-words">
                    {String(rows[Number(popupFeature.id)][field] ?? "—")}
                  </dd>
                </div>
              ))}
            </dl>
          </MapPopup>
        )}
      </MapSurface>
      <p role="status" className="text-xs text-muted-foreground">
        {error
          ? "Map unavailable. Verified locations remain in the table. "
          : !loaded
            ? "Loading map. "
            : ""}
        {data.collection.features.length} mapped; {data.invalid} invalid;{" "}
        {data.omitted} omitted by display limits. Overview basemap. Locations
        stay in Nova.
      </p>
      {options.layer === "arc" && (
        <p className="text-xs text-muted-foreground">
          Arcs show relationships, not road routes or measured travel distances.
        </p>
      )}
      {selectedFeature && (
        <p className="text-sm" role="status">
          Selected: {String(selectedFeature.properties?.label)}
        </p>
      )}
      <details
        open={tableExpanded}
        onToggle={(event) => setTableExpanded(event.currentTarget.open)}
      >
        <summary className="cursor-pointer py-2 text-sm focus-visible:outline-2 focus-visible:outline-ring">
          View locations
        </summary>
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                <th className="p-2">Location</th>
                <th className="p-2">Geometry</th>
                <th className="p-2">Coordinates (longitude, latitude)</th>
                <th className="p-2">Selection</th>
              </tr>
            </thead>
            <tbody>
              {data.collection.features.map((feature) => (
                <tr
                  key={feature.id}
                  aria-selected={selected.value === feature.id}
                >
                  <td className="p-2">{String(feature.properties?.label)}</td>
                  <td className="p-2">{feature.geometry.type}</td>
                  <td className="max-w-64 break-words p-2">
                    {JSON.stringify(feature.geometry.coordinates).slice(0, 240)}
                    {JSON.stringify(feature.geometry.coordinates).length > 240
                      ? "…"
                      : ""}
                  </td>
                  <td className="p-2">
                    <Button
                      variant="ghost"
                      disabled={readOnly}
                      onClick={() => choose(Number(feature.id))}
                    >
                      Select {String(feature.properties?.label)}
                    </Button>
                  </td>
                </tr>
              ))}
              {data.invalidRows.map((index) => (
                <tr key={`invalid-${index}`}>
                  <td className="p-2">
                    {String(
                      rows[index][fields.label ?? ""] ?? `Row ${index + 1}`,
                    ).slice(0, 300)}
                  </td>
                  <td colSpan={3} className="p-2 text-muted-foreground">
                    Invalid or missing coordinates; excluded from map
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
