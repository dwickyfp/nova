import { createRef, useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import {
  Map,
  MapControls,
  MapMarker,
  MarkerContent,
  MarkerPopup,
  MarkerTooltip,
  MarkerLabel,
  MapPopup,
  MapGeoJSON,
  MapClusterLayer,
  MapArc,
  MapRoute,
  RouteMarker,
  RouteProgress,
  type MapRef,
} from ".";
import {
  businessMapStyle,
  localMapRequest,
} from "@/features/studio/intelligent-ui/map-style";
import "@/styles/index.css";

afterEach(() => vi.restoreAllMocks());
const coordinates: [number, number][] = [
  [106.8, -6.2],
  [107.6, -6.9],
];
const points: GeoJSON.FeatureCollection<GeoJSON.Point> = {
  type: "FeatureCollection",
  features: [0, 1].map((i) => ({
    type: "Feature",
    id: i,
    properties: { row: i },
    geometry: { type: "Point", coordinates: [106.8, -6.2] },
  })),
};

it("uses local resources, working controls, and retains the camera across style changes", async () => {
  const ref = createRef<MapRef>();
  const commit = vi.fn();
  const style = businessMapStyle();
  const screen = await render(
    <div style={{ height: 320 }}>
      <Map
        ref={ref}
        styles={{ light: style, dark: style }}
        center={coordinates[0]}
        zoom={9}
        transformRequest={localMapRequest}
        onViewportCommit={commit}
      >
        <MapControls showCompass showFullscreen />
        <MapClusterLayer data={points} />
      </Map>
    </div>,
  );
  await expect
    .element(screen.getByLabelText("Map viewport"))
    .toHaveAttribute("aria-busy", "false");
  const before = ref.current!.getZoom();
  await screen.getByRole("button", { name: "Zoom in", exact: true }).click();
  await expect.poll(() => ref.current!.getZoom()).toBeCloseTo(before + 1, 0);
  await expect.poll(() => commit.mock.calls.length).toBeGreaterThan(0);
  ref.current!.jumpTo({ bearing: 45, pitch: 30 });
  await screen.getByRole("button", { name: "Reset bearing to north" }).click();
  await expect.poll(() => ref.current!.getBearing()).toBeCloseTo(0);
  const getStyle = ref.current!.getStyle();
  expect(getStyle.layers.some((l) => l.type === "symbol")).toBe(true);
  expect(getStyle.glyphs).toContain("/maps/fonts/");
  expect(() => localMapRequest("https://external.invalid/tiles")).toThrow();
  expect(localMapRequest("/maps/land-110m.geojson").url).toContain(
    location.origin,
  );
});

it("opens marker popups with the keyboard, closes with Escape, and restores focus", async () => {
  const style = businessMapStyle();
  const screen = await render(
    <div style={{ height: 320 }}>
      <Map
        styles={{ light: style, dark: style }}
        center={coordinates[0]}
        zoom={10}
        transformRequest={localMapRequest}
      >
        <MapMarker longitude={106.8} latitude={-6.2}>
          <MarkerContent>
            <button type="button">Branch details</button>
            <MarkerLabel>Branch A</MarkerLabel>
          </MarkerContent>
          <MarkerTooltip>Verified location</MarkerTooltip>
          <MarkerPopup closeButton>
            <p>Branch popup information</p>
          </MarkerPopup>
        </MapMarker>
      </Map>
    </div>,
  );
  const button = screen.getByRole("button", { name: "Branch details" });
  await expect.element(button).toBeVisible();
  await button.click();
  await expect
    .element(screen.getByText("Branch popup information"))
    .toBeVisible();
  await userEvent.keyboard("{Escape}");
  await expect
    .element(screen.getByText("Branch popup information"))
    .not.toBeInTheDocument();
  await expect.element(button).toHaveFocus();
});

it("renders route progress, markers, arcs, GeoJSON and standalone popup using local layers", async () => {
  const ref = createRef<MapRef>();
  const style = businessMapStyle();
  const closed = vi.fn();
  const screen = await render(
    <div style={{ height: 320 }}>
      <Map
        ref={ref}
        styles={{ light: style, dark: style }}
        center={coordinates[0]}
        zoom={8}
        transformRequest={localMapRequest}
      >
        <MapRoute id="delivery" coordinates={coordinates} progress={0.5} active>
          <RouteProgress />
          <RouteMarker at="progress">
            <MarkerContent>
              <span>Halfway marker</span>
            </MarkerContent>
          </RouteMarker>
        </MapRoute>
        <MapArc
          id="relationships"
          data={[{ id: "a", from: coordinates[0], to: coordinates[1] }]}
        />
        <MapGeoJSON
          id="area"
          data={{
            type: "Feature",
            properties: { id: 1 },
            geometry: {
              type: "Polygon",
              coordinates: [
                [
                  [106, -7],
                  [108, -7],
                  [108, -6],
                  [106, -7],
                ],
              ],
            },
          }}
        />
        <MapPopup
          longitude={106.8}
          latitude={-6.2}
          closeButton
          onClose={closed}
        >
          <p>Selected geometry</p>
        </MapPopup>
      </Map>
    </div>,
  );
  await expect.element(screen.getByText("Halfway marker")).toBeVisible();
  await expect
    .poll(
      () =>
        ref.current!.getStyle().layers.filter((l) => l.type === "line").length,
    )
    .toBeGreaterThanOrEqual(3);
  await screen.getByRole("button", { name: "Close popup" }).click();
  expect(closed).toHaveBeenCalledOnce();
});

it("reports initialization failure without leaving an endless loader", async () => {
  const error = vi.fn();
  const screen = await render(
    <div style={{ height: 320 }}>
      <Map
        styles={{
          light: "https://blocked.invalid/style",
          dark: "https://blocked.invalid/style",
        }}
        transformRequest={() => {
          throw new Error("Blocked");
        }}
        onMapError={error}
      />
    </div>,
  );
  await expect.poll(() => error.mock.calls.length).toBeGreaterThan(0);
  await expect
    .element(screen.getByLabelText("Map viewport"))
    .toHaveAttribute("aria-busy", "false");
});

it("handles a rejected fullscreen request with a visible message", async () => {
  vi.spyOn(HTMLElement.prototype, "requestFullscreen").mockRejectedValue(
    new Error("Unavailable"),
  );
  function Example() {
    const [ready, setReady] = useState(false);
    return (
      <div style={{ height: 320 }}>
        <Map blank onReady={() => setReady(true)}>
          {ready && <MapControls showFullscreen />}
        </Map>
      </div>
    );
  }
  const screen = await render(<Example />);
  await screen.getByRole("button", { name: "Toggle fullscreen" }).click();
  await expect
    .element(screen.getByText("Fullscreen is unavailable."))
    .toBeVisible();
});
