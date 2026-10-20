import { z } from "zod";

const field = z.string().min(1).max(256);
export const mapOptionsSchema = z
  .object({
    controls: z.boolean().optional(),
    advanced: z.boolean().optional(),
    // Accepted so saved programs still validate; maps have no locate control.
    locate: z.boolean().optional(),
    editable: z.boolean().optional(),
    simulation: z.boolean().optional(),
    popupFields: z.array(field).max(8).optional(),
    layer: z.enum(["geometry", "route", "arc"]).optional(),
    progress: field.optional(),
    toLongitude: field.optional(),
    toLatitude: field.optional(),
  })
  .strict();
export type MapOptions = z.infer<typeof mapOptionsSchema>;

export const mapViewSchema = z
  .object({
    center: z.tuple([
      z.number().finite().min(-180).max(180),
      z.number().finite().min(-90).max(90),
    ]),
    zoom: z.number().finite().min(0).max(22),
    bearing: z.number().finite().min(-180).max(180),
    pitch: z.number().finite().min(0).max(60),
  })
  .strict();
export type MapView = z.infer<typeof mapViewSchema>;

export function mapView(value: unknown): MapView | undefined {
  const result = mapViewSchema.safeParse(value);
  return result.success ? result.data : undefined;
}
