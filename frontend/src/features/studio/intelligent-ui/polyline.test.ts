import { describe, expect, it } from "vitest";
import { decodePolyline } from "./polyline";

describe("decodePolyline", () => {
  it("reads the reference polyline as longitude, latitude", () => {
    expect(decodePolyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")).toEqual([
      [-120.2, 38.5],
      [-120.95, 40.7],
      [-126.453, 43.252],
    ]);
  });

  it("stops at a truncated line instead of inventing a point", () => {
    expect(decodePolyline("_p~iF~ps|U_ulL")).toEqual([[-120.2, 38.5]]);
  });
});
