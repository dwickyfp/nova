import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { useStateField } from "@openuidev/react-lang";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import mapWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useTheme } from "@/context/theme-provider";
import { Button } from "@/components/ui/button";
import { geoBounds, geoFeatures } from "./geometry";
import { UiRuntimeContext } from "./runtime-context";

export default function GeoMap({ p }: { p: Record<string, unknown> }) {
  const rows = useMemo(() => Array.isArray(p.data) ? p.data as Record<string, unknown>[] : [], [p.data]);
  const fields = useMemo(() => ({
    longitude: p.longitude as string | undefined, latitude: p.latitude as string | undefined,
    geometry: p.geometry as string | undefined, label: p.label as string | undefined,
  }), [p.longitude, p.latitude, p.geometry, p.label]);
  const data = useMemo(() => geoFeatures(rows, fields), [rows, fields]);
  const selected = useStateField("selected", p.selected);
  const { readOnly } = useContext(UiRuntimeContext);
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [tableExpanded, setTableExpanded] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const { resolvedTheme } = useTheme();
  const selectRef = useRef((row: number) => { if (!readOnly) selected.setValue(row); });
  selectRef.current = (row) => { if (!readOnly) selected.setValue(row); };
  useEffect(() => { if (error) setTableExpanded(true); }, [error]);
  useEffect(() => {
    let disposed = false;
    setLoaded(false);
    setError(false);
    void import("maplibre-gl").then(async ({ Map, NavigationControl, setWorkerUrl }) => {
      await import("maplibre-gl/dist/maplibre-gl.css");
      if (disposed || !container.current) return;
      setWorkerUrl(mapWorkerUrl);
      const css = getComputedStyle(container.current);
      const palette = document.createElement("canvas");
      palette.width = palette.height = 1;
      const context = palette.getContext("2d", { willReadFrequently: true });
      const token = (name: string) => {
        const value = css.getPropertyValue(name).trim();
        if (!context) return value;
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = value;
        context.fillRect(0, 0, 1, 1);
        const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
        return `rgba(${r},${g},${b},${a / 255})`;
      };
      const map = new Map({
        container: container.current, center: [0, 0], zoom: 1, attributionControl: {},
        style: {
          version: 8,
          sources: { land: { type: "geojson", data: "/maps/land-110m.geojson", attribution: "Natural Earth · public domain · overview basemap" } },
          layers: [
            { id: "background", type: "background", paint: { "background-color": token("--background") } },
            { id: "land", type: "fill", source: "land", paint: { "fill-color": token("--muted"), "fill-outline-color": token("--border") } },
          ],
        },
        // A governed map never requests remote tiles, fonts, sprites, or geocoding.
        transformRequest: (url) => {
          const resolved = new URL(url, window.location.href);
          if (resolved.origin !== window.location.origin) throw new Error("External map resource refused");
          return { url: resolved.href };
        },
      });
      mapRef.current = map;
      map.addControl(new NavigationControl({ visualizePitch: false }), "top-right");
      map.on("load", () => {
        if (disposed) return;
        const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
        map.addSource("points", { type: "geojson", data: empty, cluster: true, clusterRadius: 40 });
        map.addSource("geometry", { type: "geojson", data: empty });
        map.addLayer({ id: "polygons", source: "geometry", type: "fill",
          filter: ["==", ["geometry-type"], "Polygon"],
          paint: { "fill-color": token("--chart-1"), "fill-opacity": 0.25 } });
        map.addLayer({ id: "outlines", source: "geometry", type: "line",
          paint: { "line-color": token("--chart-1"), "line-width": 2 } });
        map.addLayer({ id: "points", source: "points", type: "circle",
          paint: { "circle-color": token("--primary"), "circle-radius": ["case", ["has", "point_count"], 14, 7],
            "circle-stroke-color": token("--background"), "circle-stroke-width": 2 } });
        map.on("click", "points", async (event) => {
          const feature = event.features?.[0];
          if (!feature) return;
          if (feature.properties?.cluster_id !== undefined) {
            try {
              const zoom = await (map.getSource("points") as GeoJSONSource).getClusterExpansionZoom(Number(feature.properties.cluster_id));
              if (!disposed && feature.geometry.type === "Point")
                map.easeTo({ center: feature.geometry.coordinates as [number, number], zoom, duration: 0 });
            } catch { if (!disposed) setError(true); }
          } else selectRef.current(Number(feature.properties?.row));
        });
        map.on("click", "polygons", (event) => {
          const row = event.features?.[0]?.properties?.row;
          if (row !== undefined) selectRef.current(Number(row));
        });
        map.on("click", "outlines", (event) => {
          const row = event.features?.[0]?.properties?.row;
          if (row !== undefined) selectRef.current(Number(row));
        });
        setLoaded(true);
      });
      map.on("error", () => { if (!disposed) setError(true); });
    }).catch(() => { if (!disposed) setError(true); });
    return () => { disposed = true; mapRef.current?.remove(); mapRef.current = null; };
  }, [resolvedTheme]);
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map) return;
    (map.getSource("points") as GeoJSONSource).setData({
      ...data.collection, features: data.collection.features.filter((f) => f.geometry.type === "Point"),
    });
    (map.getSource("geometry") as GeoJSONSource).setData({
      ...data.collection, features: data.collection.features.filter((f) => f.geometry.type !== "Point"),
    });
    const bounds = geoBounds(data.collection.features);
    if (bounds) map.fitBounds(bounds, { padding: 36, maxZoom: 12, animate: false });
  }, [loaded, data]);
  useEffect(() => {
    const map = mapRef.current;
    if (!loaded || !map) return;
    const row = typeof selected.value === "number" ? selected.value : -1;
    map.setPaintProperty("points", "circle-stroke-width", ["case", ["==", ["get", "row"], row], 4, 2]);
    map.setPaintProperty("polygons", "fill-opacity", ["case", ["==", ["get", "row"], row], 0.5, 0.25]);
    map.setPaintProperty("outlines", "line-width", ["case", ["==", ["get", "row"], row], 4, 2]);
  }, [loaded, selected.value]);
  const selectedFeature = data.collection.features.find((f) => f.id === selected.value);
  const choose = (row: number) => {
    if (!readOnly) selected.setValue(row);
    const feature = data.collection.features.find((f) => f.id === row);
    const bounds = feature && geoBounds([feature]);
    if (bounds && loaded) mapRef.current?.fitBounds(bounds, { padding: 48, maxZoom: 12, animate: false });
  };
  return <section className="min-w-0 space-y-2" aria-label={String(p.title ?? "Business map")}>
    <p className="text-sm font-medium">{String(p.title ?? "Business map")}</p>
    <div ref={container} className="h-80 w-full rounded-lg border border-border" aria-label="Map viewport" aria-busy={!loaded && !error} />
    <p role="status" className="text-xs text-muted-foreground">
      {error ? "Map unavailable. Verified locations remain in the table. " : !loaded ? "Loading map. " : ""}
      {data.collection.features.length} mapped; {data.invalid} invalid; {data.omitted} omitted by display limits.
      {" "}Overview basemap. Locations stay in Nova.
    </p>
    {selectedFeature && <p className="text-sm" role="status">Selected: {String(selectedFeature.properties?.label)}</p>}
    <details open={tableExpanded} onToggle={(event) => setTableExpanded(event.currentTarget.open)}>
      <summary className="cursor-pointer py-2 text-sm focus-visible:outline-2 focus-visible:outline-ring">View locations</summary>
      <div className="max-h-64 overflow-auto">
        <table className="w-full text-left text-sm">
          <thead><tr><th className="p-2">Location</th><th className="p-2">Geometry</th><th className="p-2">Coordinates (longitude, latitude)</th><th className="p-2">Selection</th></tr></thead>
          <tbody>{data.collection.features.map((feature) => <tr key={feature.id} aria-selected={selected.value === feature.id}>
            <td className="p-2">{String(feature.properties?.label)}</td><td className="p-2">{feature.geometry.type}</td>
            <td className="max-w-64 break-words p-2">{JSON.stringify(feature.geometry.coordinates).slice(0, 240)}{JSON.stringify(feature.geometry.coordinates).length > 240 ? "…" : ""}</td>
            <td className="p-2"><Button variant="ghost" disabled={readOnly} onClick={() => choose(Number(feature.id))}>Select {String(feature.properties?.label)}</Button></td>
          </tr>)}
          {data.invalidRows.map((index) => <tr key={`invalid-${index}`}>
            <td className="p-2">{String(rows[index][fields.label ?? ""] ?? `Row ${index + 1}`).slice(0, 300)}</td>
            <td colSpan={3} className="p-2 text-muted-foreground">Invalid or missing coordinates; excluded from map</td>
          </tr>)}</tbody>
        </table>
      </div>
    </details>
  </section>;
}
