import { describe, expect, it } from "vitest";
import { geoBounds, geoFeatures, geometryPositions, MAX_GEO_FEATURES, parseGeometry } from "./geometry";
import { validateProgram } from "./validator";

describe("business geometry", () => {
  it("accepts explicit engine geometry without swapping coordinates", () => {
    expect(parseGeometry("POINT (106.8272 -6.1754)")).toEqual({
      type: "Point", coordinates: [106.8272, -6.1754],
    });
    expect(parseGeometry("LINESTRING (0 0, 1 1)")?.type).toBe("LineString");
    const polygon = parseGeometry('{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}');
    expect(polygon?.type).toBe("Polygon");
    expect(geometryPositions(polygon!)).toHaveLength(4);
  });

  it.each([
    null, "POINT (NaN 0)", "POINT (0 91)", "POINT (181 0)", "POINT Z (1 2 3)",
    { type: "Point", coordinates: ["106", -6] },
    { type: "LineString", coordinates: [[0, 0]] },
    { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1]]] },
    { type: "Point", coordinates: [Infinity, 0] },
    { type: "MultiPoint", coordinates: [[0, 0]] },
  ])("rejects malformed or unsupported geometry %j", (value) => {
    expect(parseGeometry(value)).toBeNull();
  });

  it("counts invalid and truncated rows and preserves source selection identity", () => {
    const result = geoFeatures([
      { lon: null, lat: 0 }, { lon: 106, lat: -6, name: "Branch" },
      ...Array.from({ length: MAX_GEO_FEATURES }, () => ({ lon: 0, lat: 0 })),
    ], { longitude: "lon", latitude: "lat", label: "name" });
    expect(result.invalid).toBe(1);
    expect(result.omitted).toBe(2);
    expect(result.collection.features).toHaveLength(999);
    expect(result.collection.features[0]).toMatchObject({ id: 1, properties: { row: 1, label: "Branch" } });
  });

  it("reads SQL decimal coordinate strings without coercing empty, boolean, or formatted values", () => {
    const { collection, invalidRows } = geoFeatures([
      { x: "106.8272", y: "-6.1754" },
      ...["", " ", true, null, "106,8272", "1e2", "0x10", "181", "NaN"].map((x) => ({ x, y: 0 })),
    ], { longitude: "x", latitude: "y" });
    expect(collection.features).toHaveLength(1);
    expect(collection.features[0].geometry).toEqual({ type: "Point", coordinates: [106.8272, -6.1754] });
    expect(invalidRows).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("fits antimeridian points without zooming out across the entire world", () => {
    const { collection } = geoFeatures([{ x: 179, y: -2 }, { x: -179, y: 3 }], { longitude: "x", latitude: "y" });
    expect(geoBounds(collection.features)).toEqual([[179, -2], [181, 3]]);
    expect(geoBounds([])).toBeNull();
  });

  it("requires governed evidence even outside a business question", () => {
    expect(() => validateProgram('root=GeoMap([{longitude:106,latitude:-6}],"longitude","latitude")')).toThrow();
    const result = validateProgram(
      '$selected=-1\nroot=Col([GeoMap(@Evidence("r"),"longitude","latitude",null,"branch","Branches",$selected)])',
      { r: [{ longitude: 106, latitude: -6, branch: "A" }] },
      true,
    );
    expect(result.evidence_ids).toEqual(["r"]);
    expect(result.state).toEqual({ $selected: -1 });
  });
});
