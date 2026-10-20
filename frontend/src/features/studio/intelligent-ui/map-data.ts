import { geoFeatures, parseGeometry } from "./geometry";
import type { MapOptions } from "./map-options";

export function mapData(
  rows: Record<string, unknown>[],
  fields: {
    longitude?: string;
    latitude?: string;
    geometry?: string;
    label?: string;
  },
  options: MapOptions,
) {
  const data = geoFeatures(rows, fields);
  data.collection.features = data.collection.features.filter((feature) => {
    const row = rows[Number(feature.id)];
    let valid = true;
    if (options.layer === "route")
      valid = feature.geometry.type === "LineString";
    if (options.layer === "arc") {
      const destination = geoFeatures([row], {
        longitude: options.toLongitude,
        latitude: options.toLatitude,
      }).collection.features[0];
      valid =
        feature.geometry.type === "Point" &&
        destination?.geometry.type === "Point";
      if (
        valid &&
        feature.geometry.type === "Point" &&
        destination.geometry.type === "Point"
      ) {
        const geometry = parseGeometry({
          type: "LineString",
          coordinates: [
            feature.geometry.coordinates,
            destination.geometry.coordinates,
          ],
        });
        if (geometry) feature.geometry = geometry;
      }
    }
    if (options.progress) {
      const value = row[options.progress];
      if (
        typeof value !== "number" ||
        !Number.isFinite(value) ||
        value < 0 ||
        value > 1
      )
        valid = false;
    }
    if (!valid) {
      data.invalid++;
      data.invalidRows.push(Number(feature.id));
    }
    return valid;
  });
  return data;
}
