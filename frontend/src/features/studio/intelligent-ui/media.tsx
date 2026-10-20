import { lazy, Suspense, useContext, useEffect, useRef, useState } from "react";
import { UiRuntimeContext } from "./runtime-context";
import { uiApi } from "./api";
import mapWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "./map-popup.css";

import { MAPCN_ENABLED } from "./map-flag";
const MapcnPublicMap = lazy(() => import("./public-map"));

type Props = Record<string, unknown>;
export function PublicMedia({ name, p }: { name: string; p: Props }) {
  const { source } = useContext(UiRuntimeContext);
  const [loaded, setLoaded] = useState<{ key: string; value: Props } | null>(
    null,
  );
  const [failedKey, setFailedKey] = useState<string | null>(null);
  const { viewport: _viewport, routeSelected: _routeSelected, ...requestProps } = p;
  const key = JSON.stringify(requestProps);
  const requestKey = JSON.stringify([source, name, requestProps]);
  const result = loaded?.key === requestKey ? loaded.value : null;
  const error = failedKey === requestKey;
  useEffect(() => {
    const abort = new AbortController();
    if (!source || name === "YouTube") return;
    void uiApi
      .media(source, name, JSON.parse(key), abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) {
          setLoaded({ key: requestKey, value });
          setFailedKey(null);
        }
      })
      .catch(() => {
        if (!abort.signal.aborted) setFailedKey(requestKey);
      });
    return () => abort.abort();
  }, [key, name, source, requestKey]);
  if (name === "YouTube")
    return (
      <a
        href={`https://www.youtube.com/watch?v=${String(p.videoId)}`}
        target="_blank"
        rel="noopener noreferrer"
        referrerPolicy="no-referrer"
        className="inline-flex min-h-11 min-w-11 items-center text-sm text-primary underline focus-visible:outline-2 focus-visible:outline-ring dark:text-accent-foreground"
      >
        Watch {String(p.title)}
      </a>
    );
  if (name === "Map" && (error || !source)) {
    const places = (p.markers ?? []) as { props: { label: string } }[];
    return (
      <section aria-label={String(p.label)} className="min-w-0">
        <p role="status" className="text-sm text-muted-foreground">
          The public map is unavailable. Places are listed below.
        </p>
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm">
          {places.map((place, index) => <li key={index}>{place.props.label}</li>)}
        </ol>
      </section>
    );
  }
  if (!source)
    return (
      <p className="text-sm text-muted-foreground">
        Public media is available in the original conversation.
      </p>
    );
  if (error)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Public media could not load. The description remains available:{" "}
        {String(p.alt ?? p.label ?? "")}.
      </p>
    );
  if (!result)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading public {name === "Map" ? "map" : "image"}…
      </p>
    );
  if (name === "Map")
    return MAPCN_ENABLED ? <Suspense fallback={<p role="status">Loading map…</p>}><MapcnPublicMap key={requestKey} p={p} result={result} /></Suspense> : <PublicMap key={requestKey} p={p} result={result} />;
  return (
    <figure className="min-w-0">
      <img
        src={String(result.url)}
        alt={String(p.alt)}
        referrerPolicy="no-referrer"
        loading="lazy"
        className="max-h-80 w-full rounded-lg object-cover"
        onError={() => setFailedKey(requestKey)}
      />
      <figcaption className="mt-2 text-xs text-muted-foreground">
        {String(p.caption ?? result.description ?? "Public image")} ·{" "}
        <a
          href={String(result.source)}
          target="_blank"
          rel="noopener noreferrer"
          className="underline focus-visible:outline-2 focus-visible:outline-ring"
        >
          Wikimedia source and license
        </a>
      </figcaption>
    </figure>
  );
}

function PublicMap({ p, result }: { p: Props; result: Props }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState(false);
  const markers = (result.markers ?? []) as {
    latitude: number;
    longitude: number;
    label: string;
  }[];
  const key = JSON.stringify(result);
  useEffect(() => {
    let dispose = () => {};
    let cancelled = false;
    void import("maplibre-gl")
      .then(async ({ Map, Marker, Popup, LngLatBounds, setWorkerUrl }) => {
        await import("maplibre-gl/dist/maplibre-gl.css");
        if (!ref.current || cancelled) return;
        setWorkerUrl(mapWorkerUrl);
        const data = JSON.parse(key) as {
          markers: typeof markers;
          geometry?: { type: string; coordinates: number[][] };
        };
        const map = new Map({
          container: ref.current,
          style: "https://tiles.openfreemap.org/styles/liberty",
          center: data.markers.length
            ? [data.markers[0].longitude, data.markers[0].latitude]
            : [0, 0],
          zoom: 11,
          attributionControl: {},
        });
        dispose = () => map.remove();
        const bounds = new LngLatBounds();
        data.markers.forEach((m) => {
          const label = document.createElement("p");
          label.textContent = m.label;
          new Marker()
            .setLngLat([m.longitude, m.latitude])
            .setPopup(new Popup().setDOMContent(label))
            .addTo(map);
          bounds.extend([m.longitude, m.latitude]);
        });
        if (data.markers.length > 1)
          map.fitBounds(bounds, { padding: 36, maxZoom: 13, animate: false });
        map.on("load", () => {
          if (!data.geometry) return;
          map.addSource("public-route", {
            type: "geojson",
            data: {
              type: "Feature",
              properties: {},
              geometry: data.geometry as GeoJSON.LineString,
            },
          });
          map.addLayer({
            id: "public-route",
            type: "line",
            source: "public-route",
            paint: {
              "line-color": getComputedStyle(document.documentElement)
                .getPropertyValue("--primary")
                .trim(),
              "line-width": 4,
            },
          });
        });
        map.on("error", () => {
          if (!cancelled) setError(true);
        });
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
      dispose();
    };
  }, [key]);
  return (
    <section aria-label={String(p.label)} className="nova-public-map min-w-0">
      {error ? (
        <p role="status" className="mb-2 text-sm text-muted-foreground">
          The public map is unavailable. Places are listed below.
        </p>
      ) : null}
      <div ref={ref} className="h-80 w-full rounded-lg border border-border" />
      <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm">
        {markers.map((m, i) => (
          <li key={i}>{m.label}</li>
        ))}
      </ol>
      {p.route === true ? (
        <p className="mt-2 text-xs text-muted-foreground">
          {result.geometry
            ? "Driving route from OSRM; verify conditions before travelling."
            : "Driving route unavailable. Places remain listed."}
        </p>
      ) : null}
    </section>
  );
}
