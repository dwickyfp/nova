/**
 * Road geometry for a route map: the public OSRM server turns ordered stops into
 * the line a vehicle would drive. The stops' coordinates are sent to that public
 * service; the business map falls back to straight lines when it cannot answer.
 */

export const OSRM_ROUTE_URL =
  "https://router.project-osrm.org/route/v1/driving";
/** The public server answers routes of up to this many waypoints. */
const MAX_WAYPOINTS = 25;
const TIMEOUT_MS = 8000;

export type RoadRoute = {
  coordinates: [number, number][];
  /** Driving distance along the roads, in kilometres. */
  km: number;
};

const cache = new Map<string, Promise<RoadRoute | null>>();

/** The road route through these stops, in order, or null when it is unavailable. */
export function roadRoute(
  stops: [number, number][],
): Promise<RoadRoute | null> {
  if (stops.length < 2) return Promise.resolve(null);
  const key = stops
    .map(([lon, lat]) => `${lon.toFixed(6)},${lat.toFixed(6)}`)
    .join(";");
  const known = cache.get(key);
  if (known) return known;
  const pending = fetchLegs(stops).catch(() => null);
  cache.set(key, pending);
  // A failed request is tried again on the next render, not remembered.
  void pending.then((result) => {
    if (!result) cache.delete(key);
  });
  return pending;
}

async function fetchLegs(stops: [number, number][]): Promise<RoadRoute | null> {
  const coordinates: [number, number][] = [];
  let km = 0;
  // Long routes are asked in overlapping chunks so each stays within the limit.
  for (let start = 0; start < stops.length - 1; start += MAX_WAYPOINTS - 1) {
    const chunk = stops.slice(start, start + MAX_WAYPOINTS);
    const part = await fetchRoute(chunk);
    if (!part) return null;
    coordinates.push(
      ...(coordinates.length ? part.coordinates.slice(1) : part.coordinates),
    );
    km += part.km;
  }
  return { coordinates, km };
}

async function fetchRoute(
  stops: [number, number][],
): Promise<RoadRoute | null> {
  const path = stops
    .map(([lon, lat]) => `${lon.toFixed(6)},${lat.toFixed(6)}`)
    .join(";");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(
      `${OSRM_ROUTE_URL}/${path}?overview=full&geometries=geojson&steps=false`,
      { signal: controller.signal },
    );
    if (!response.ok) return null;
    const body = (await response.json()) as {
      code?: string;
      routes?: { distance?: number; geometry?: { coordinates?: unknown } }[];
    };
    const route = body.code === "Ok" ? body.routes?.[0] : undefined;
    const line = route?.geometry?.coordinates;
    if (
      !Array.isArray(line) ||
      line.length < 2 ||
      typeof route?.distance !== "number"
    )
      return null;
    const coordinates = line
      .filter(
        (point): point is [number, number] =>
          Array.isArray(point) &&
          point.length >= 2 &&
          Number.isFinite(point[0]) &&
          Number.isFinite(point[1]),
      )
      .map(([lon, lat]) => [lon, lat] as [number, number]);
    return coordinates.length >= 2
      ? { coordinates, km: route.distance / 1000 }
      : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}
