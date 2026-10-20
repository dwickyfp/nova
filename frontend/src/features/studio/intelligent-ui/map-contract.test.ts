import { describe, expect, it } from "vitest";
import { validateProgram } from "./validator";
import { validateProgram as validateV2 } from "./validator-v2";
import { mapData } from "./map-data";
import { mapView } from "./map-options";
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
const route =
  '$s=-1\n$v=null\nroot=Col([GeoMap(@Evidence("routes"),null,null,"shape","name","Routes",$s,{layer:"route",progress:"progress"},$v)])';
const arc =
  '$s=-1\nroot=Col([GeoMap(@Evidence("routes"),"lon","lat",null,"name","Connections",$s,{layer:"arc",toLongitude:"endLon",toLatitude:"endLat"})])';

describe("mapcn evidence and state contract", () => {
  it("resolves route and arc selections from validated result rows", () => {
    for (const program of [route, arc]) {
      expect(
        validateProgram(program, evidence, true, { $s: 0 }).selections[0].row,
      ).toEqual(evidence.routes[0]);
      expect(
        validateProgram(program, evidence, true, { $s: 1 }).selections[0].row,
      ).toBeNull();
    }
  });
  it.each([
    '{url:"https://example.test/data"}',
    '{layer:"heatmap"}',
    "{locate:true}",
    "{editable:true}",
    "{simulation:true}",
    '{layer:"arc"}',
    '{progress:"progress"}',
  ])("refuses unsafe or unsupported business map options %s", (options) => {
    expect(() =>
      validateProgram(
        route.replace('{layer:"route",progress:"progress"}', options),
        evidence,
        true,
      ),
    ).toThrow();
  });
  it("accepts a bounded saved camera, refuses invalid coordinates and fields", () => {
    const view = { center: [106.8, -6.2], zoom: 12, bearing: 0, pitch: 45 };
    expect(() =>
      validateProgram(route, evidence, true, { $v: view }),
    ).not.toThrow();
    for (const invalid of [
      { ...view, zoom: 50 },
      { ...view, center: [190, 0] },
      { ...view, url: "external" },
    ])
      expect(() =>
        validateProgram(route, evidence, true, { $v: invalid }),
      ).toThrow();
    expect(mapView({ ...view, pitch: Infinity })).toBeUndefined();
  });
  it("keeps public viewport and route selection out of the media request", () => {
    const program =
      '$v=null\n$r=0\nroot=Col([Map([MapMarker(-6.2,106.8,"Public place")],true,"Public",{advanced:true},$v,$r)])';
    const parsed = validateProgram(program, {}, false, { $r: 2 });
    const media = parsed.media.find((m) => m.type === "Map")!;
    expect(media.props).not.toHaveProperty("viewport");
    expect(media.props).not.toHaveProperty("routeSelected");
    expect(media.props.options).toEqual({ advanced: true });
    expect(() => validateProgram(program, {}, false, { $r: 3 })).toThrow();
    expect(() => validateProgram(program, {}, true)).toThrow();
  });
  it("preserves v2 behavior and does not reinterpret v3 options as v2", () => {
    const old =
      '$s=-1\nroot=Col([GeoMap(@Evidence("routes"),"lon","lat",null,"name","Locations",$s)])';
    expect(validateV2(old, evidence, true).catalog_version).toBe(
      "nova-openui-2",
    );
    expect(validateProgram(old, evidence, true).state).toEqual(
      validateV2(old, evidence, true).state,
    );
    expect(() => validateV2(route, evidence, true)).toThrow();
  });
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

describe("basemaps", () => {
  it("loads CARTO only for public places and keeps business maps local", () => {
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
