import { afterEach, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { userEvent } from "vitest/browser";
import type * as MapLibreGL from "maplibre-gl";
import { useMap } from "@/components/ui/map";
import { ThemeProvider } from "@/context/theme-provider";
import { MapSurface } from "./map-surface";
import { PUBLIC_3D_STYLE, type Bounds } from "./map-style";
import "@/styles/index.css";

afterEach(() => vi.restoreAllMocks());
// Each test loads real maps and animates the camera; a busy runner needs more than the default.
vi.setConfig({ testTimeout: 45_000 });

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
    timeout: 15000,
  });
  map!.jumpTo({ pitch: 45, bearing: 30, zoom: map!.getZoom() + 1 });

  await screen.getByRole("button", { name: "Show all locations" }).click();
  await vi.waitFor(
    () => {
      expect(map!.getPitch()).toBe(0);
      expect(map!.getBearing()).toBe(0);
      expect(fitted(map!)).toBe(true);
    },
    { timeout: 15000 },
  );
});

it("puts its actions inside the map as labelled icons, with no row above it", async () => {
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
  const fit = screen.getByRole("button", { name: "Show all locations" });
  await expect.element(fit).toBeVisible();
  const frame = screen.getByLabelText("Map viewport").element();
  const buttons = [...screen.container.querySelectorAll("button")];
  expect(buttons.map((button) => button.getAttribute("aria-label"))).toEqual([
    "Show all locations",
    "3D view",
    "Tilt view",
    "Reset view",
  ]);
  for (const button of buttons) {
    // Icons only, inside the map frame, at least 32 px.
    expect(frame.contains(button)).toBe(true);
    expect(button.textContent).toBe("");
    const box = button.getBoundingClientRect();
    expect(box.width).toBeGreaterThanOrEqual(32);
    expect(box.height).toBeGreaterThanOrEqual(32);
  }
  // Top-right, clear of the attribution in the bottom-right corner.
  const toolbar = screen.getByRole("toolbar", { name: "Map controls" }).element();
  const frameBox = frame.getBoundingClientRect();
  const toolbarBox = toolbar.getBoundingClientRect();
  expect(frameBox.right - toolbarBox.right).toBeLessThan(16);
  expect(toolbarBox.top - frameBox.top).toBeLessThan(16);
  await expect
    .element(screen.getByRole("button", { name: "3D view" }))
    .toHaveAttribute("aria-pressed", "false");
  await userEvent.hover(fit);
  await expect.element(screen.getByRole("tooltip")).toHaveTextContent("Show all locations");
  for (const name of ["Zoom in", "Find my location", "Toggle fullscreen"])
    expect(screen.container.querySelector(`[aria-label="${name}"]`)).toBeNull();
});

it("hides the actions when the program turns controls off", async () => {
  let map: MapLibreGL.Map | null = null;
  const screen = await render(
    <ThemeProvider>
      <MapSurface
        bounds={bounds}
        viewport={null}
        options={{ controls: false }}
        onError={vi.fn()}
      >
        <Probe onMap={(instance) => (map = instance)} />
      </MapSurface>
    </ThemeProvider>,
  );
  await vi.waitFor(() => expect(map).not.toBeNull());
  expect(screen.container.querySelector("button")).toBeNull();
});

it("frames a single location at a city zoom, not the closest zoom", async () => {
  let map: MapLibreGL.Map | null = null;
  const point: Bounds = [
    [110.42, -6.97],
    [110.42, -6.97],
  ];
  const screen = await render(
    <ThemeProvider>
      <div style={{ width: 720 }}>
        <MapSurface bounds={point} viewport={null} onError={vi.fn()}>
          <Probe onMap={(instance) => (map = instance)} />
        </MapSurface>
      </div>
    </ThemeProvider>,
  );
  await vi.waitFor(() => expect(map?.loaded()).toBe(true), { timeout: 15000 });
  map!.jumpTo({ center: [100, 0], zoom: 4 });
  await screen.getByRole("button", { name: "Show all locations" }).click();
  await vi.waitFor(() => {
    expect(map!.getCenter().lng).toBeCloseTo(110.42, 1);
    expect(map!.getCenter().lat).toBeCloseTo(-6.97, 1);
    expect(map!.getZoom()).toBeGreaterThan(10);
    expect(map!.getZoom()).toBeLessThanOrEqual(13);
  }, { timeout: 5000 });
});

it("follows the data again after Show all, so a resize keeps every location in view", async () => {
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
  await vi.waitFor(() => expect(map && fitted(map)).toBe(true), {
    timeout: 15000,
  });
  // The user pans away, then asks for every location again.
  map!.panBy([400, 200], { animate: false }, { originalEvent: new MouseEvent("mousemove") });
  expect(fitted(map!)).toBe(false);
  await vi.waitFor(() => expect(onViewport).toHaveBeenCalled());
  onViewport.mockClear();
  await screen.getByRole("button", { name: "Show all locations" }).click();
  await vi.waitFor(() => expect(fitted(map!)).toBe(true), { timeout: 5000 });
  // The fitted camera is saved as the user's view.
  await vi.waitFor(() => expect(onViewport).toHaveBeenCalled());
  // A rotated phone or a narrower panel still shows every location.
  (screen.getByTestId("host").element() as HTMLElement).style.width = "360px";
  await vi.waitFor(() => {
    expect(map!.getCanvas().clientWidth).toBeLessThanOrEqual(360);
    expect(fitted(map!)).toBe(true);
  }, { timeout: 5000 });
});

function stubThreeDStyle(response: () => Promise<Response>) {
  const real = window.fetch.bind(window);
  return vi.spyOn(window, "fetch").mockImplementation((input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    return url.startsWith(PUBLIC_3D_STYLE) ? response() : real(input, init);
  });
}

it("tilts into 3D and back, keeps it across a re-render, and starts flat for a new answer", async () => {
  // A local stand-in for the online 3D basemap.
  stubThreeDStyle(async () =>
    Response.json({
      version: 8,
      sources: {},
      layers: [{ id: "background", type: "background", paint: { "background-color": "#eee" } }],
    }),
  );
  let map: MapLibreGL.Map | null = null;
  const surface = (key: string) => (
    <ThemeProvider>
      <div style={{ width: 720 }}>
        <MapSurface key={key} bounds={bounds} viewport={null} onError={vi.fn()}>
          <Probe onMap={(instance) => (map = instance)} />
        </MapSurface>
      </div>
    </ThemeProvider>
  );
  const screen = await render(surface("first"));
  await vi.waitFor(() => expect(map?.loaded()).toBe(true), { timeout: 15000 });
  const threeD = screen.getByRole("button", { name: "3D view" });
  await threeD.click();
  await expect.element(threeD).toHaveAttribute("aria-pressed", "true");
  await vi.waitFor(() => {
    expect(map!.getPitch()).toBe(60);
    expect(map!.getZoom()).toBeGreaterThanOrEqual(15);
  }, { timeout: 5000 });
  // A pressed trigger keeps its tooltip closed until the pointer comes back.
  await userEvent.unhover(threeD);
  await userEvent.hover(threeD);
  await expect.element(screen.getByRole("tooltip")).toHaveTextContent("2D view");
  await screen.rerender(surface("first"));
  await expect.element(threeD).toHaveAttribute("aria-pressed", "true");
  await threeD.click();
  await expect.element(threeD).toHaveAttribute("aria-pressed", "false");
  await vi.waitFor(() => expect(map!.getPitch()).toBe(0), { timeout: 5000 });
  await threeD.click();
  await expect.element(threeD).toHaveAttribute("aria-pressed", "true");
  await screen.rerender(surface("second"));
  await expect
    .element(screen.getByRole("button", { name: "3D view" }))
    .toHaveAttribute("aria-pressed", "false");
});

it("returns to the street map when the 3D basemap cannot load, and says why", async () => {
  stubThreeDStyle(() => Promise.reject(new TypeError("Failed to fetch")));
  let map: MapLibreGL.Map | null = null;
  const onError = vi.fn();
  const screen = await render(
    <ThemeProvider>
      <MapSurface bounds={bounds} viewport={null} onError={onError}>
        <Probe onMap={(instance) => (map = instance)} />
      </MapSurface>
    </ThemeProvider>,
  );
  await vi.waitFor(() => expect(map?.loaded()).toBe(true), { timeout: 15000 });
  const threeD = screen.getByRole("button", { name: "3D view" });
  await threeD.click();
  await expect.element(threeD).toHaveAttribute("aria-disabled", "true");
  await expect.element(threeD).toHaveAttribute("aria-pressed", "false");
  await vi.waitFor(() => {
    expect(map!.getPitch()).toBe(0);
    // The street basemap is back, not a blank canvas.
    expect(map!.isStyleLoaded()).toBe(true);
    expect(map!.getStyle().layers.length).toBeGreaterThan(1);
  }, { timeout: 15000 });
  // An unavailable control stays focusable for its tooltip but does nothing.
  (threeD.element() as HTMLButtonElement).click();
  await expect.element(threeD).toHaveAttribute("aria-pressed", "false");
  await userEvent.unhover(threeD);
  await userEvent.hover(threeD);
  await expect.element(screen.getByRole("tooltip")).toHaveTextContent("3D view could not load");
  expect(onError).not.toHaveBeenCalled();
});

it("leaves page scrolling alone and removes every map it drew", async () => {
  const maps: MapLibreGL.Map[] = [];
  const screen = await render(
    <ThemeProvider>
      {[0, 1, 2, 3].map((index) => (
        <MapSurface key={index} bounds={bounds} viewport={null} basemap="bundled" onError={vi.fn()}>
          <Probe onMap={(instance) => !maps.includes(instance) && maps.push(instance)} />
        </MapSurface>
      ))}
    </ThemeProvider>,
  );
  await vi.waitFor(() => expect(maps).toHaveLength(4));
  // A plain wheel scrolls the page; Ctrl/⌘ + wheel zooms the map.
  for (const map of maps) expect(map.cooperativeGestures.isEnabled()).toBe(true);
  await screen.unmount();
  for (const map of maps) expect((map as unknown as { _removed: boolean })._removed).toBe(true);
});
