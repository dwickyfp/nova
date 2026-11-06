import { describe, expect, it } from "vitest";
import { caption, routeGroups } from "./route-data";

const rows = [
  { route: "Area 2", stop_seq: 1, stop: "C", latitude: 3.52, longitude: 98.7 },
  { route: "Area 1", stop_seq: 2, stop: "B", latitude: 3.59, longitude: 98.67 },
  { route: "Area 1", stop_seq: 1, stop: "A", latitude: 3.58, longitude: 98.66 },
  { route: "Area 1", stop_seq: null, stop: "X", latitude: 3.5, longitude: 98.6 },
  { route: "Area 1", stop_seq: 3, stop: "Y", latitude: null, longitude: 98.6 },
];

describe("routeGroups", () => {
  it("joins located stops per route in their stated order", () => {
    const { routes, invalidRows } = routeGroups(
      rows, { longitude: "longitude", latitude: "latitude", label: "stop" }, "stop_seq", "route",
    );
    expect(routes.map((route) => route.key)).toEqual(["Area 1", "Area 2"]);
    expect(routes[0].stops.map((stop) => stop.label)).toEqual(["A", "B"]);
    expect(routes[0].coordinates).toEqual([[98.66, 3.58], [98.67, 3.59]]);
    expect(routes[0].lineKm).toBeGreaterThan(1);
    // A stop without an order or without coordinates is not drawn.
    expect(invalidRows).toEqual([3, 4]);
  });

  it("treats ungrouped stops as one route", () => {
    const { routes } = routeGroups(rows.slice(1, 3), { longitude: "longitude", latitude: "latitude" }, "stop_seq");
    expect(routes).toHaveLength(1);
    expect(routes[0].key).toBe("Route");
  });
});

describe("caption", () => {
  it("reads unit suffixes as units", async () => {
    expect(caption("route_km")).toBe("Route (km)");
    expect(caption("original_order_km")).toBe("Original order (km)");
    expect(caption("saving_pct")).toBe("Saving (%)");
    expect(caption("outlets.demand")).toBe("Demand");
  });
});
