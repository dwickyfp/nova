import { afterEach, describe, expect, it, vi } from "vitest";
import { OSRM_ROUTE_URL, roadRoute } from "./route-road";

afterEach(() => vi.restoreAllMocks());

const reply = (coordinates: number[][], distance: number) =>
  new Response(
    JSON.stringify({
      code: "Ok",
      routes: [{ distance, geometry: { coordinates } }],
    }),
  );

describe("roadRoute", () => {
  it("follows the road geometry and distance OSRM returns", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      reply(
        [
          [98.66, 3.58],
          [98.665, 3.585],
          [98.67, 3.59],
        ],
        2350,
      ),
    );
    const road = await roadRoute([
      [98.66, 3.58],
      [98.67, 3.59],
    ]);
    expect(road).toEqual({
      coordinates: [
        [98.66, 3.58],
        [98.665, 3.585],
        [98.67, 3.59],
      ],
      km: 2.35,
    });
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      `${OSRM_ROUTE_URL}/98.660000,3.580000;98.670000,3.590000?overview=full&geometries=geojson`,
    );
  });

  it("asks long routes in chunks of at most 25 stops and joins them", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (url) => {
        const path = String(url).split("/driving/")[1].split("?")[0].split(";");
        const points = path.map((pair) => pair.split(",").map(Number));
        return reply(points, 1000);
      });
    const stops = Array.from(
      { length: 30 },
      (_, i) => [98.6 + i / 1000, 3.5] as [number, number],
    );
    const road = await roadRoute(stops);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(road?.coordinates).toHaveLength(30);
    expect(road?.km).toBe(2);
  });

  it("returns nothing when the service cannot answer, so the map keeps straight lines", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("busy", { status: 429 }),
    );
    expect(
      await roadRoute([
        [98.7, 3.6],
        [98.71, 3.61],
      ]),
    ).toBeNull();
  });
});
