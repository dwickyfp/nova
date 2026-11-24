import { describe, expect, it } from "vitest";
import { mapData } from "./map-data";
import { mapOptionsSchema } from "./map-options";
import {
  businessMapStyle,
  PUBLIC_3D_STYLE,
  PUBLIC_MAP_STYLES,
} from "./map-style";

const evidence = {
  routes: [
    {
      name: "Verified route",
      shape: "LINESTRING (106.8 -6.2,107.6 -6.9)",
      progress: 0.4,
      lon: 106.8,
      lat: -6.2,
      endLon: 107.6,
      endLat: -6.9,
    },
    {
      name: "Invalid endpoint",
      shape: "POINT (106.8 -6.2)",
      progress: 2,
      lon: 106.8,
      lat: -6.2,
      endLon: null,
      endLat: -6.9,
    },
  ],
};
describe("map rows", () => {
  it("counts invalid route progress and arc endpoints without inventing geometry", () => {
    const routes = mapData(
      evidence.routes,
      { geometry: "shape" },
      { layer: "route", progress: "progress" },
    );
    expect(routes.invalidRows).toEqual([1]);
    expect(routes.collection.features[0].geometry.type).toBe("LineString");
    const arcs = mapData(
      evidence.routes,
      { longitude: "lon", latitude: "lat" },
      { layer: "arc", toLongitude: "endLon", toLatitude: "endLat" },
    );
    expect(arcs.invalidRows).toEqual([1]);
    expect(arcs.collection.features[0].geometry.coordinates).toEqual([
      [106.8, -6.2],
      [107.6, -6.9],
    ]);
  });
});

describe("route options", () => {
  it("accept a stop order and a route group on the route layer, never data", () => {
    expect(mapOptionsSchema.safeParse({
      layer: "route", sequence: "stop_seq", group: "route", routeFields: ["route_km"],
      popupFields: ["stop"],
    }).success).toBe(true);
    expect(mapOptionsSchema.safeParse({ layer: "route", rows: [{ lat: 1 }] }).success).toBe(false);
    expect(mapOptionsSchema.safeParse({ routeFields: ["a", "b", "c", "d", "e"] }).success).toBe(false);
  });
});

describe("basemaps", () => {
  it("streets come from CARTO and the bundled overview stays offline", () => {
    for (const url of Object.values(PUBLIC_MAP_STYLES))
      expect(new URL(url).hostname).toBe("basemaps.cartocdn.com");
    expect(PUBLIC_3D_STYLE).toBe(
      "https://tiles.openfreemap.org/styles/liberty",
    );
    const business = JSON.stringify(businessMapStyle());
    expect(business).not.toMatch(/https?:\/\//);
    expect(business).not.toContain("carto");
    expect(business).not.toContain("openfreemap");
  });
});
