import { expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import type * as MapLibreGL from "maplibre-gl";
import { useMap } from "@/components/ui/map";
import { ThemeProvider } from "@/context/theme-provider";
import { MapSurface } from "./map-surface";
import type { Bounds } from "./map-style";
import "@/styles/index.css";

const bounds: Bounds = [
  [106.8, -7.3],
  [112.8, -6.1],
];

function Probe({ onMap }: { onMap: (map: MapLibreGL.Map) => void }) {
  const { map } = useMap();
  if (map) onMap(map);
  return null;
}

// Every location is visible and the camera is not merely zoomed out to the world.
function fitted(map: MapLibreGL.Map) {
  const view = map.getBounds();
  return (
    view.getWest() <= bounds[0][0] &&
    view.getEast() >= bounds[1][0] &&
    view.getSouth() <= bounds[0][1] &&
    view.getNorth() >= bounds[1][1] &&
    view.getEast() - view.getWest() < 3 * (bounds[1][0] - bounds[0][0])
  );
}

it("keeps data in view while the answer settles, and saves only a camera the user moved", async () => {
  let map: MapLibreGL.Map | null = null;
  const onViewport = vi.fn();
  const screen = await render(
    <ThemeProvider>
      <div data-testid="host" style={{ width: 960 }}>
        <MapSurface
          bounds={bounds}
          viewport={null}
          onViewport={onViewport}
          onError={vi.fn()}
        >
          <Probe onMap={(instance) => (map = instance)} />
        </MapSurface>
      </div>
    </ThemeProvider>,
  );
  await expect
    .element(screen.getByLabelText("Map viewport"))
    .toHaveAttribute("aria-busy", "false");
  const host = screen.getByTestId("host").element() as HTMLElement;
  await vi.waitFor(() => expect(fitted(map!)).toBe(true));

  host.style.width = "420px";
  await vi.waitFor(() => {
    expect(map!.getCanvas().clientWidth).toBeLessThanOrEqual(420);
    expect(fitted(map!)).toBe(true);
  });
  expect(onViewport).not.toHaveBeenCalled();

  map!.zoomTo(
    map!.getZoom() + 2,
    { animate: false },
    {
      originalEvent: new WheelEvent("wheel"),
    },
  );
  const zoom = map!.getZoom();
  await vi.waitFor(() => expect(onViewport).toHaveBeenCalled());

  host.style.width = "960px";
  await vi.waitFor(() =>
    expect(map!.getCanvas().clientWidth).toBeGreaterThan(900),
  );
  expect(map!.getZoom()).toBeCloseTo(zoom, 5);
});

it("shows every location from a tilted, rotated camera", async () => {
  let map: MapLibreGL.Map | null = null;
  const screen = await render(
    <ThemeProvider>
      <div style={{ width: 720 }}>
        <MapSurface bounds={bounds} viewport={null} onError={vi.fn()}>
          <Probe onMap={(instance) => (map = instance)} />
        </MapSurface>
      </div>
    </ThemeProvider>,
  );
  await vi.waitFor(() => expect(map && fitted(map)).toBe(true), {
    timeout: 5000,
  });
  map!.jumpTo({ pitch: 45, bearing: 30, zoom: map!.getZoom() + 1 });

  await screen.getByRole("button", { name: "Show all locations" }).click();
  await vi.waitFor(
    () => {
      expect(map!.getPitch()).toBe(0);
      expect(map!.getBearing()).toBe(0);
      expect(fitted(map!)).toBe(true);
    },
    { timeout: 5000 },
  );
});

it("renders no navigation controls on the canvas", async () => {
  const screen = await render(
    <ThemeProvider>
      <MapSurface
        bounds={bounds}
        viewport={null}
        options={{ advanced: true, locate: true }}
        onError={vi.fn()}
      >
        {null}
      </MapSurface>
    </ThemeProvider>,
  );
  await expect
    .element(screen.getByRole("button", { name: "Show all locations" }))
    .toBeVisible();
  // 3D loads external building tiles, so verified business maps never offer it.
  expect(screen.container.textContent).not.toContain("3D view");
  for (const name of [
    "Zoom in",
    "Zoom out",
    "Reset bearing to north",
    "Find my location",
    "Toggle fullscreen",
  ])
    expect(screen.container.querySelector(`[aria-label="${name}"]`)).toBeNull();
});
