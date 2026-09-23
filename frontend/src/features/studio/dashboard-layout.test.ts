import { describe, expect, it } from "vitest";
import type { StudioDashboardTile } from "@/features/agents/api";
import { canPlaceTile, firstFreePosition, tilePixelRect } from "./dashboard-layout";

const tile = (id: string, x: number, y: number, w = 3, h = 2): StudioDashboardTile => ({
  tile_id: id,
  artifact_id: id,
  x, y, w, h,
});

describe("dashboard grid placement", () => {
  it("fills four 3 × 2 spots in a 6 × 4 canvas", () => {
    const placed: StudioDashboardTile[] = [];
    for (const [x, y] of [[0, 0], [3, 0], [0, 2], [3, 2]]) {
      expect(firstFreePosition(placed)).toEqual({ x, y });
      placed.push(tile(String(placed.length), x, y));
    }
    expect(firstFreePosition(placed)).toBeNull();
  });

  it("rejects collisions and out-of-bounds resize while allowing a tile to move", () => {
    const placed = [tile("a", 0, 0), tile("b", 3, 0)];
    expect(canPlaceTile(placed, tile("c", 2, 0))).toBe(false);
    expect(canPlaceTile(placed, tile("a", 0, 2))).toBe(true);
    expect(canPlaceTile(placed, tile("a", 0, 0, 7, 2))).toBe(false);
  });

  it("sizes tile cards against the full canvas", () => {
    expect(tilePixelRect(tile("a", 0, 0), 1200, 800, 8)).toEqual({
      left: 0,
      top: 0,
      width: 596,
      height: 396,
    });
    expect(tilePixelRect(tile("b", 3, 2), 1200, 800, 8)).toEqual({
      left: 604,
      top: 404,
      width: 596,
      height: 396,
    });
  });
});
