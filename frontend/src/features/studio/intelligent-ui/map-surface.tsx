import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Box, Compass, Rotate3d, Scan } from "lucide-react";
import type { StyleSpecification } from "maplibre-gl";
import {
  Map,
  MapControlButton,
  MapControlGroup,
  MapControlStack,
  useMap,
  type MapRef,
  type MapViewport,
} from "@/components/ui/map";
import { motionDuration } from "@/components/ui/map/tokens";
import { useTheme } from "@/context/theme-provider";
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

// The fit Show all uses: flat and north-up, since a fit from a tilted or
// rotated camera can leave locations off-canvas.
const FIT_ALL = { padding: FIT_PADDING, maxZoom: 13, pitch: 0, bearing: 0 };

function MapActions({
  bounds,
  options,
  follow,
  threeD,
  onThreeD,
  threeDFailed,
}: {
  bounds: Bounds | null;
  options: MapOptions;
  follow: Flag;
  threeD: boolean;
  onThreeD?: (on: boolean) => void;
  threeDFailed: boolean;
}) {
  const { map } = useMap();
  const fit = () => {
    if (!map || !bounds) return;
    onThreeD?.(false);
    // The fitted camera is committed as the user's view; afterwards the camera
    // follows the data again, so a resize or rotation keeps every location in view.
    follow.current = false;
    const canvas = map.getCanvas();
    const size = `${canvas.clientWidth}x${canvas.clientHeight}`;
    map.once("moveend", () => {
      follow.current = true;
      // A resize during the animation would otherwise land on a stale frame.
      if (`${canvas.clientWidth}x${canvas.clientHeight}` !== size)
        map.fitBounds(bounds, { ...FIT_ALL, animate: false });
    });
    map.fitBounds(bounds, { ...FIT_ALL, duration: motionDuration(300) });
  };
  const toggleThreeD = () => {
    if (!map || !onThreeD) return;
    follow.current = false;
    onThreeD(!threeD);
    map.easeTo(
      threeD
        ? { pitch: 0, bearing: 0, duration: motionDuration(500) }
        : {
            pitch: 60,
            // Liberty extrudes buildings from zoom 14; mapcn's example uses 15.
            zoom: Math.max(map.getZoom(), 15),
            duration: motionDuration(500),
          },
    );
  };
  return (
    <MapControlStack>
      <MapControlGroup>
        <MapControlButton
          label="Show all locations"
          onClick={fit}
          disabled={!bounds}
        >
          <Scan className="size-4" aria-hidden />
        </MapControlButton>
        {onThreeD && (
          <MapControlButton
            label="3D view"
            tooltip={
              threeDFailed
                ? "3D view could not load"
                : threeD
                  ? "2D view"
                  : "3D view"
            }
            pressed={threeD}
            disabled={threeDFailed}
            onClick={toggleThreeD}
          >
            <Box className="size-4" aria-hidden />
          </MapControlButton>
        )}
        {options.advanced && (
          <MapControlButton
            label="Tilt view"
            onClick={() => {
              follow.current = false;
              map?.easeTo({ pitch: 45, duration: motionDuration(300) });
            }}
          >
            <Rotate3d className="size-4" aria-hidden />
          </MapControlButton>
        )}
        {options.advanced && (
          <MapControlButton
            label="Reset view"
            onClick={() => {
              follow.current = false;
              onThreeD?.(false);
              map?.resetNorthPitch({ duration: motionDuration(300) });
            }}
          >
            <Compass className="size-4" aria-hidden />
          </MapControlButton>
        )}
      </MapControlGroup>
    </MapControlStack>
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
  aside,
  bounds,
  focusBounds,
  options = {},
  viewport,
  onViewport,
  onError,
  onReady,
  basemap = "configured",
}: {
  children: ReactNode;
  /** A panel beside the map frame, sharing the frame's height. */
  aside?: ReactNode;
  bounds: Bounds | null;
  focusBounds?: Bounds | null;
  options?: MapOptions;
  viewport?: unknown;
  onViewport?: (view: MapViewport) => void;
  onError: () => void;
  onReady?: () => void;
  basemap?: "bundled" | "configured";
}) {
  const { resolvedTheme } = useTheme();
  const follow = useRef(true);
  const mapRef = useRef<MapRef | null>(null);
  // Business and public maps both show the online street basemap; "bundled"
  // keeps the offline overview (tests, previews without network).
  const streets = basemap !== "bundled";
  // Only the 3D toggle reaches 60°, so a saved camera at that pitch reopens in 3D.
  const [threeD, setThreeD] = useState(
    () => streets && (mapView(viewport)?.pitch ?? 0) >= 59.5,
  );
  // The 3D basemap is a separate online service: when it fails, the map
  // returns to 2D and the 3D control explains why it is unavailable.
  const [threeDFailed, setThreeDFailed] = useState(false);
  const threeDShown = useRef(threeD);
  threeDShown.current = threeD;
  const styles = useMemo(() => {
    if (streets && threeD)
      return { light: PUBLIC_3D_STYLE, dark: PUBLIC_3D_STYLE };
    if (streets) return PUBLIC_MAP_STYLES;
    const style: StyleSpecification = businessMapStyle();
    return { light: style, dark: style };
  }, [resolvedTheme, streets, threeD]);
  const view = mapView(viewport);
  return (
    <div className="min-w-0">
      <div
        className={
          aside
            ? "grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_15rem]"
            : "contents"
        }
      >
        <div className="h-80 w-full min-w-0 overflow-hidden rounded-lg border border-border">
          <Map
            ref={mapRef}
            styles={styles}
            theme={resolvedTheme}
            viewport={view}
            onMapError={(event) => {
              // An event means the started map failed to load a resource;
              // without one MapLibre itself could not start.
              if (event && threeDShown.current) {
                threeDShown.current = false;
                setThreeD(false);
                setThreeDFailed(true);
                mapRef.current?.easeTo({ pitch: 0, bearing: 0, duration: 0 });
                return;
              }
              onError();
            }}
            onReady={() => {
              onReady?.();
            }}
            transformRequest={streets ? undefined : localMapRequest}
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
            {options.controls !== false && (
              <MapActions
                bounds={bounds}
                options={options}
                follow={follow}
                threeD={threeD}
                onThreeD={streets ? setThreeD : undefined}
                threeDFailed={threeDFailed}
              />
            )}
          </Map>
        </div>
        {aside}
      </div>
    </div>
  );
}
