import { useMemo, useState } from "react";
import { MapMarker, MapRoute, MarkerContent, MarkerTooltip } from "@/components/ui/map";
import { mapColor } from "@/components/ui/map/tokens";
import { cn } from "@/lib/utils";
import { placeBounds } from "./geometry";
import { MapSurface } from "./map-surface";
import { decodePolyline } from "./polyline";

export type CheckedPlace = {
  label: string;
  lat: number;
  lon: number;
  /** verified: near the city it names; corrected: moved to it; approximate: not checked. */
  check: "verified" | "corrected" | "approximate";
};

export type StoredRoute = { line: string; distance_m: number; duration_s: number };

export type PlaceMapProps = {
  title: string;
  places: CheckedPlace[];
  routes: StoredRoute[];
  unavailable?: string | null;
  optimized?: boolean;
};

const ROUTE_TOKEN = "--chart-4";

function formatDistance(metres: number): string {
  const km = metres / 1000;
  return `${km.toLocaleString(undefined, { maximumFractionDigits: km < 10 ? 1 : 0 })} km`;
}

function formatTravelTime(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${minutes} min`;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/**
 * Public places and the road routes between them, as the server checked and
 * stored them: the page never calls the routing service. The fastest route is
 * drawn first; an alternative can be chosen beside the map.
 */
export default function PlaceMap({ title, places, routes, unavailable, optimized }: PlaceMapProps) {
  const lines = useMemo(() => routes.map((route) => decodePolyline(route.line)), [routes]);
  const [active, setActive] = useState(0);
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  // Every route, so Show all frames whichever alternative is drawn.
  const bounds = useMemo(() => placeBounds(places, lines), [places, lines]);
  const color = mapColor(ROUTE_TOKEN);
  const routed = routes.length > 0;
  const approximate = places.some((place) => place.check === "approximate");
  const chosen = routes[active];

  const aside = routed ? (
    <div className="flex min-w-0 flex-col gap-2 rounded-lg border p-2 sm:h-80" role="radiogroup" aria-label="Routes">
      {routes.map((route, index) => (
        <button
          key={index}
          type="button"
          role="radio"
          aria-checked={active === index}
          tabIndex={active === index ? 0 : -1}
          onClick={() => setActive(index)}
          onKeyDown={(event) => {
            const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[event.key];
            if (!step) return;
            event.preventDefault();
            const next = (index + step + routes.length) % routes.length;
            setActive(next);
            const group = event.currentTarget.parentElement;
            group?.querySelectorAll<HTMLElement>('[role="radio"]')[next]?.focus();
          }}
          className={cn(
            "flex w-full flex-col gap-0.5 rounded-md px-3 py-2 text-left transition-colors focus-visible:outline-2 focus-visible:outline-ring",
            active === index ? "bg-muted" : "hover:bg-muted/60",
          )}
        >
          <span className="flex items-center gap-2 text-sm font-medium">
            <span
              aria-hidden="true"
              className={cn("h-1 w-4 shrink-0 rounded-full", active !== index && "opacity-40")}
              style={{ background: `var(${ROUTE_TOKEN})` }}
            />
            {index === 0 ? "Fastest route" : `Alternative ${index}`}
          </span>
          <span className="pl-6 text-xs text-muted-foreground tabular-nums">
            {formatDistance(route.distance_m)} · {formatTravelTime(route.duration_s)}
          </span>
        </button>
      ))}
      <ol className="mt-auto space-y-0.5 px-3 pb-1 text-xs text-muted-foreground" aria-label="Stops in order">
        {places.map((place, index) => (
          <li key={`${place.label}-${index}`} className="flex gap-2">
            <span className="w-4 shrink-0 text-right tabular-nums">{index + 1}</span>
            <span className="min-w-0 truncate">
              {place.label}
              {place.check === "approximate" ? " (approximate)" : ""}
            </span>
          </li>
        ))}
      </ol>
    </div>
  ) : null;

  return (
    <section className="min-w-0 space-y-2" aria-label={title} data-component="PlaceMap">
      <div className="flex min-h-7 flex-wrap items-center gap-x-2 gap-y-1">
        <p className="min-w-0 flex-1 text-sm font-medium">{title}</p>
        {optimized ? (
          <span className="rounded-sm bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            Shortest order from {places[0]?.label}
          </span>
        ) : null}
      </div>
      <MapSurface aside={aside} bounds={bounds} onReady={() => setLoaded(true)} onError={() => setError(true)}>
        {/* Ids name the role as well as the route: a line keeps its id for
            life, so the chosen route remounts on top instead of renaming one. */}
        {lines.map((line, index) =>
          index === active ? null : (
            <MapRoute
              key={`alt-${index}`}
              id={`place-alt-${index}`}
              coordinates={line}
              color={color}
              width={3}
              opacity={0.35}
              interactive
              onClick={() => setActive(index)}
            />
          ),
        )}
        {lines[active] ? (
          <MapRoute
            key={`active-${active}`}
            id={`place-active-${active}`}
            coordinates={lines[active]}
            color={color}
            width={5}
            opacity={0.95}
            interactive={false}
          />
        ) : null}
        {places.map((place, index) => (
          <MapMarker key={`${place.label}-${index}`} longitude={place.lon} latitude={place.lat}>
            <MarkerContent>
              <span
                aria-label={`${routed ? `Stop ${index + 1}: ` : ""}${place.label}${place.check === "approximate" ? ", approximate position" : ""}`}
                className={cn(
                  "flex size-5 items-center justify-center rounded-full border-2 border-card text-[10px] leading-none font-semibold text-white tabular-nums shadow-sm",
                  place.check === "approximate" && "outline-2 outline-offset-1 outline-dashed outline-foreground/60",
                )}
                style={{ background: `var(${ROUTE_TOKEN})` }}
              >
                {routed ? index + 1 : null}
              </span>
            </MarkerContent>
            <MarkerTooltip>
              <p className="text-xs font-medium">{place.label}</p>
              {place.check === "approximate" ? (
                <p className="text-xs text-muted-foreground">Approximate position</p>
              ) : null}
            </MarkerTooltip>
          </MapMarker>
        ))}
      </MapSurface>
      <p role="status" className="text-xs text-muted-foreground">
        {error ? "Map unavailable; the places stay listed. " : !loaded ? "Loading map. " : ""}
        {chosen
          ? `${places.length} places by road: ${formatDistance(chosen.distance_m)}, about ${formatTravelTime(chosen.duration_s)}.`
          : `${places.length} ${places.length === 1 ? "place" : "places"}.`}
        {unavailable ? ` ${unavailable}` : ""}
        {approximate ? " Positions marked with a dashed ring are approximate." : ""}
      </p>
      {!routed ? (
        <ul className="sr-only" aria-label="Places">
          {places.map((place, index) => (
            <li key={`${place.label}-${index}`}>{place.label}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
