import { geoFeatures } from "./geometry";

export type RouteStop = {
  /** Index of the row in the bound evidence. */
  row: number;
  /** 1-based position in the visit order, as the evidence states it. */
  order: number;
  label: string;
  coordinates: [number, number];
};

export type RouteGroup = {
  key: string;
  stops: RouteStop[];
  coordinates: [number, number][];
  /** Great-circle length of the drawn line, not a road distance. */
  lineKm: number;
};

const EARTH_RADIUS_KM = 6371.0088;

/** A stop's stated position in its route; a missing or non-numeric one is none. */
export function stopOrder(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function haversineKm([lon1, lat1]: [number, number], [lon2, lat2]: [number, number]) {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad;
  const dLon = (lon2 - lon1) * rad;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}

/**
 * Ordered stops as routes: rows grouped by `group` (a driver, an area) and joined
 * in `sequence` order. Rows without coordinates or a numeric order are counted as
 * invalid and left off the map.
 */
export function routeGroups(
  rows: Record<string, unknown>[],
  fields: { longitude?: string; latitude?: string; label?: string },
  sequence: string,
  group?: string,
): { routes: RouteGroup[]; invalidRows: number[] } {
  const located = geoFeatures(rows, fields);
  const invalidRows = [...located.invalidRows];
  const byGroup = new Map<string, RouteStop[]>();
  for (const feature of located.collection.features) {
    const row = Number(feature.id);
    const order = stopOrder(rows[row][sequence]);
    if (feature.geometry.type !== "Point" || order === null) {
      invalidRows.push(row);
      continue;
    }
    const key = group ? String(rows[row][group] ?? "—") : "Route";
    const stops = byGroup.get(key) ?? [];
    stops.push({
      row,
      order,
      label: String(feature.properties?.label ?? `Stop ${order}`),
      coordinates: feature.geometry.coordinates as [number, number],
    });
    byGroup.set(key, stops);
  }
  const routes = [...byGroup.entries()]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([key, stops]) => {
      const ordered = [...stops].sort((a, b) => a.order - b.order);
      const coordinates = ordered.map((stop) => stop.coordinates);
      let lineKm = 0;
      for (let index = 1; index < coordinates.length; index += 1)
        lineKm += haversineKm(coordinates[index - 1], coordinates[index]);
      return { key, stops: ordered, coordinates, lineKm };
    });
  return { routes, invalidRows: invalidRows.sort((a, b) => a - b) };
}

const UNITS: Record<string, string> = { km: "km", m: "m", pct: "%", min: "min" };

/** "route_km" reads "Route (km)", "saving_pct" reads "Saving (%)". */
export function caption(field: string): string {
  const words = field.replace(/^.*\./, "").split("_").filter(Boolean);
  const unit = words.length > 1 ? UNITS[words[words.length - 1]] : undefined;
  const text = (unit ? words.slice(0, -1) : words).join(" ");
  const label = text.charAt(0).toUpperCase() + text.slice(1);
  return unit ? `${label} (${unit})` : label;
}
