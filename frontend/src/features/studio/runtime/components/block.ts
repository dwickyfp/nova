/**
 * A registry component as the stream and the stored turn carry it, checked
 * against the same Zod schemas the server validated it with. React-free, so
 * the protocol parser can use it.
 */
import {
  COMPONENTS,
  type ComponentName,
  type DatavizChartProps,
  type GeoMapProps,
  type KpiTilesProps,
} from "./schemas";

export type ComponentBlock =
  | { component: "DatavizChart"; version: number; props: DatavizChartProps }
  | { component: "KpiTiles"; version: number; props: KpiTilesProps }
  | { component: "GeoMap"; version: number; props: GeoMapProps };

/** The block, typed, when its name, version and props match the registry; null otherwise. */
export function parseComponentBlock(value: unknown): ComponentBlock | null {
  if (!value || typeof value !== "object") return null;
  const { component, version, props } = value as Record<string, unknown>;
  if (typeof component !== "string" || !Object.prototype.hasOwnProperty.call(COMPONENTS, component)) return null;
  const entry = COMPONENTS[component as ComponentName];
  if (version !== entry.version) return null;
  const parsed = entry.schema.safeParse(props);
  return parsed.success ? ({ component, version, props: parsed.data } as ComponentBlock) : null;
}
