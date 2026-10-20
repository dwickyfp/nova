import type { StyleSpecification } from "maplibre-gl";
import { mapColor } from "@/components/ui/map/tokens";

export type Bounds = [[number, number], [number, number]];

// The CARTO basemaps mapcn uses. Only public places may load them: tile requests
// reveal the viewed area, so business maps keep the bundled overview.
export const PUBLIC_MAP_STYLES = {
  light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};

// mapcn's "OpenStreetMap 3D" example: Liberty carries extruded building layers.
// Public places only, for the same reason as the basemaps above.
export const PUBLIC_3D_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const OVERVIEW_GLYPHS = "/maps/fonts/{fontstack}/{range}.pbf";

export function businessMapStyle(): StyleSpecification {
  return {
    version: 8,
    glyphs: OVERVIEW_GLYPHS,
    sources: {
      land: {
        type: "geojson",
        data: "/maps/land-110m.geojson",
        attribution: "Natural Earth · public domain · overview basemap",
      },
    },
    layers: [
      {
        id: "background",
        type: "background",
        paint: { "background-color": mapColor("--background") },
      },
      {
        id: "land",
        type: "fill",
        source: "land",
        paint: {
          "fill-color": mapColor("--muted"),
          "fill-outline-color": mapColor("--border"),
        },
      },
    ],
  };
}

export function localMapRequest(url: string) {
  const resolved = new URL(url, window.location.href);
  if (resolved.origin !== window.location.origin)
    throw new Error("External map resource refused");
  return { url: resolved.href };
}
