import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import type { StyleSpecification } from "maplibre-gl";
import {
  Map,
  useMap,
  type MapRef,
  type MapViewport,
} from "@/components/ui/map";
import { motionDuration } from "@/components/ui/map/tokens";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/context/theme-provider";
import { useMapMetrics } from "./map-metrics";
import { mapView, type MapOptions } from "./map-options";
import {
  businessMapStyle,
  localMapRequest,
  PUBLIC_3D_STYLE,
  PUBLIC_MAP_STYLES,
  type Bounds,
} from "./map-style";

// Every side clears half of a numbered marker; the bottom also clears the attribution.
const FIT_PADDING = { top: 52, right: 52, bottom: 60, left: 52 };

// While `follow` is true the camera tracks the data bounds, including after the
// container resizes (streamed answers settle their width after the map loads).
// User gestures and explicit map actions take the camera over; only then is the
// viewport committed to UI state.
type Flag = { current: boolean };

function Camera({
  bounds,
  initialView,
  follow,
}: {
  bounds: Bounds | null;
  initialView: unknown;
  follow: Flag;
}) {
  const { map, isLoaded } = useMap();
  const fitted = useRef<string | null>(null);
  const key = JSON.stringify(bounds);
  useEffect(() => {
    if (!map || !isLoaded || fitted.current === key) return;
    const restored = fitted.current === null && mapView(initialView);
    fitted.current = key;
    follow.current = !restored;
    if (restored) map.jumpTo(restored);
    else if (bounds)
      map.fitBounds(bounds, {
        padding: FIT_PADDING,
        maxZoom: 13,
        animate: false,
      });
  }, [map, isLoaded, key, bounds, initialView, follow]);
  useEffect(() => {
    if (!map || !isLoaded) return;
    const refit = () => {
      if (follow.current && bounds)
        map.fitBounds(bounds, {
          padding: FIT_PADDING,
          maxZoom: 13,
          animate: false,
        });
    };
    const takeOver = (event: { originalEvent?: unknown }) => {
      if (event.originalEvent) follow.current = false;
    };
    map.on("resize", refit);
    map.on("movestart", takeOver);
    return () => {
      map.off("resize", refit);
      map.off("movestart", takeOver);
    };
  }, [map, isLoaded, bounds, follow]);
  return null;
}

function MapToolbar({
  mapRef,
  bounds,
  options,
  follow,
  threeD,
  onThreeD,
}: {
  mapRef: RefObject<MapRef | null>;
  bounds: Bounds | null;
  options: MapOptions;
  follow: Flag;
  threeD?: boolean;
  onThreeD?: (on: boolean) => void;
}) {
  const [advanced, setAdvanced] = useState(false);
  const fit = () => {
    follow.current = false;
    onThreeD?.(false);
    if (bounds)
      // A fit from a tilted or rotated camera can leave locations off-canvas.
      mapRef.current?.fitBounds(bounds, {
        padding: FIT_PADDING,
        maxZoom: 13,
        pitch: 0,
        bearing: 0,
        duration: motionDuration(300),
      });
  };
  return (
    <div className="flex flex-wrap gap-1">
      <Button size="sm" variant="secondary" onClick={fit} disabled={!bounds}>
        Show all locations
      </Button>
      {onThreeD && (
        <Button
          size="sm"
          variant={threeD ? "default" : "secondary"}
          aria-pressed={threeD}
          onClick={() => {
            follow.current = false;
            onThreeD(!threeD);
            const map = mapRef.current;
            map?.easeTo({
              pitch: threeD ? 0 : 60,
              // Liberty extrudes buildings from zoom 14; mapcn's example uses 15.
              ...(threeD ? {} : { zoom: Math.max(map.getZoom(), 15) }),
              duration: motionDuration(500),
            });
          }}
        >
          3D view
        </Button>
      )}
      {options.advanced && (
        <Button
          size="sm"
          variant="secondary"
          aria-expanded={advanced}
          onClick={() => setAdvanced(!advanced)}
        >
          Map view
        </Button>
      )}
      {advanced && (
        <>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              follow.current = false;
              mapRef.current?.easeTo({
                pitch: 45,
                duration: motionDuration(300),
              });
            }}
          >
            Tilt view
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              follow.current = false;
              onThreeD?.(false);
              mapRef.current?.resetNorthPitch({
                duration: motionDuration(300),
              });
            }}
          >
            Reset view
          </Button>
        </>
      )}
    </div>
  );
}

function FocusLocation({
  bounds,
  follow,
}: {
  bounds?: Bounds | null;
  follow: Flag;
}) {
  const { map, isLoaded } = useMap();
  const handled = useRef<Bounds | null>(null);
  useEffect(() => {
    if (!bounds || !map || !isLoaded || handled.current === bounds) return;
    handled.current = bounds;
    follow.current = false;
    map.fitBounds(bounds, {
      padding: FIT_PADDING,
      maxZoom: 13,
      duration: motionDuration(300),
    });
  }, [map, isLoaded, bounds, follow]);
  return null;
}

export function MapSurface({
  children,
  bounds,
  focusBounds,
  options = {},
  viewport,
  onViewport,
  onError,
  onReady,
  publicMap = false,
}: {
  children: ReactNode;
  bounds: Bounds | null;
  focusBounds?: Bounds | null;
  options?: MapOptions;
  viewport?: unknown;
  onViewport?: (view: MapViewport) => void;
  onError: () => void;
  onReady?: () => void;
  publicMap?: boolean;
}) {
  const { resolvedTheme } = useTheme();
  const metric = useMapMetrics();
  const follow = useRef(true);
  const mapRef = useRef<MapRef | null>(null);
  // Only the 3D toggle reaches 60°, so a saved camera at that pitch reopens in 3D.
  const [threeD, setThreeD] = useState(
    () => publicMap && (mapView(viewport)?.pitch ?? 0) >= 59.5,
  );
  const styles = useMemo(() => {
    if (publicMap && threeD)
      return { light: PUBLIC_3D_STYLE, dark: PUBLIC_3D_STYLE };
    if (publicMap) return PUBLIC_MAP_STYLES;
    const style: StyleSpecification = businessMapStyle();
    return { light: style, dark: style };
  }, [resolvedTheme, publicMap, threeD]);
  const view = mapView(viewport);
  return (
    <div className="min-w-0 space-y-2">
      {options.controls !== false && (
        <MapToolbar
          mapRef={mapRef}
          bounds={bounds}
          options={options}
          follow={follow}
          threeD={threeD}
          onThreeD={publicMap ? setThreeD : undefined}
        />
      )}
      <div className="h-80 w-full overflow-hidden rounded-lg border border-border">
        <Map
          ref={mapRef}
          styles={styles}
          theme={resolvedTheme}
          viewport={view}
          onMapError={() => {
            metric("failed");
            metric("fallback");
            onError();
          }}
          onReady={() => {
            metric("ready");
            onReady?.();
          }}
          transformRequest={publicMap ? undefined : localMapRequest}
          onViewportCommit={(next) => {
            if (!follow.current) {
              const normalized = {
                ...next,
                center: [
                  ((((next.center[0] + 180) % 360) + 360) % 360) - 180,
                  next.center[1],
                ],
                bearing: ((((next.bearing + 180) % 360) + 360) % 360) - 180,
              };
              const valid = mapView(normalized);
              if (valid) onViewport?.(valid);
            }
          }}
        >
          <Camera bounds={bounds} initialView={view} follow={follow} />
          <FocusLocation bounds={focusBounds} follow={follow} />
          {children}
        </Map>
      </div>
    </div>
  );
}
