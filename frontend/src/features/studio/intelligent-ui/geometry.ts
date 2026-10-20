import { wktToGeoJSON } from "@terraformer/wkt";

export type Geometry = GeoJSON.Point | GeoJSON.LineString | GeoJSON.Polygon;
export const MAX_GEO_FEATURES = 1000;
export const MAX_GEO_POSITIONS = 20_000;

function coordinate(value: unknown): unknown {
  // Nova transports SQL DECIMAL as strings to preserve precision.
  if (typeof value === "string" && value.length <= 64 &&
      /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value)) return Number(value);
  return value;
}

function position(value: unknown): value is [number, number] {
  return Array.isArray(value) && value.length === 2 &&
    value.every((n) => typeof n === "number" && Number.isFinite(n)) &&
    Math.abs(value[0]) <= 180 && Math.abs(value[1]) <= 90;
}

export function geometryPositions(geometry: Geometry): number[][] {
  if (geometry.type === "Point") return [geometry.coordinates];
  if (geometry.type === "LineString") return geometry.coordinates;
  return geometry.coordinates.flat();
}

export function parseGeometry(value: unknown): Geometry | null {
  try {
    if (typeof value === "string") {
      if (value.length > 128_000) return null;
      value = value.trim().startsWith("{") ? JSON.parse(value) : wktToGeoJSON(value);
    }
    if (!value || typeof value !== "object") return null;
    const { type, coordinates } = value as Geometry;
    const line = (v: unknown): v is number[][] =>
      Array.isArray(v) && v.length >= 2 && v.length <= MAX_GEO_POSITIONS && v.every(position);
    const ring = (v: unknown): boolean =>
      line(v) && v.length >= 4 && v[0][0] === v[v.length - 1][0] && v[0][1] === v[v.length - 1][1];
    if (type === "Point" && position(coordinates)) return { type, coordinates };
    if (type === "LineString" && line(coordinates)) return { type, coordinates };
    if (type === "Polygon" && Array.isArray(coordinates) && coordinates.length > 0 &&
      coordinates.length <= 100 && coordinates.every(ring) &&
      coordinates.flat().length <= MAX_GEO_POSITIONS) return { type, coordinates };
  } catch { /* Invalid engine/export geometry stays visible as a rejected row. */ }
  return null;
}

export function geoFeatures(
  rows: Record<string, unknown>[],
  fields: { longitude?: string; latitude?: string; geometry?: string; label?: string },
) {
  const features: GeoJSON.Feature<Geometry>[] = [];
  const invalidRows: number[] = [];
  let invalid = 0, positions = 0, omitted = 0;
  rows.forEach((row, index) => {
    if (index >= MAX_GEO_FEATURES) { omitted++; return; }
    const geometry = parseGeometry(fields.geometry ? row[fields.geometry] : {
      type: "Point", coordinates: [
        coordinate(row[fields.longitude ?? ""]), coordinate(row[fields.latitude ?? ""]),
      ],
    });
    if (!geometry) { invalid++; invalidRows.push(index); return; }
    const count = geometryPositions(geometry).length;
    if (positions + count > MAX_GEO_POSITIONS) { omitted++; return; }
    positions += count;
    features.push({
      type: "Feature", id: index, geometry,
      properties: { row: index, label: String(row[fields.label ?? ""] ?? `Row ${index + 1}`).slice(0, 300) },
    });
  });
  return { collection: { type: "FeatureCollection" as const, features }, invalid, invalidRows, omitted };
}

export function geoBounds(features: GeoJSON.Feature<Geometry>[]): [[number, number], [number, number]] | null {
  const points = features.flatMap((f) => geometryPositions(f.geometry));
  if (!points.length) return null;
  let longitudes = points.map((p) => p[0]);
  if (Math.max(...longitudes) - Math.min(...longitudes) > 180) {
    const wrapped = longitudes.map((v) => v < 0 ? v + 360 : v);
    if (Math.max(...wrapped) - Math.min(...wrapped) <
      Math.max(...longitudes) - Math.min(...longitudes)) longitudes = wrapped;
  }
  return [
    [Math.min(...longitudes), Math.min(...points.map((p) => p[1]))],
    [Math.max(...longitudes), Math.max(...points.map((p) => p[1]))],
  ];
}
