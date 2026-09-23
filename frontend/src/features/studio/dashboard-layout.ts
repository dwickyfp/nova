import type { StudioDashboardTile } from "@/features/agents/api";

export const DASHBOARD_COLUMNS = 6;
export const DASHBOARD_ROWS = 4;
export const DEFAULT_TILE_WIDTH = 3;
export const DEFAULT_TILE_HEIGHT = 2;

export function canPlaceTile(
  tiles: StudioDashboardTile[],
  candidate: StudioDashboardTile,
): boolean {
  if (
    candidate.x < 0 ||
    candidate.y < 0 ||
    candidate.w < 1 ||
    candidate.h < 1 ||
    candidate.x + candidate.w > DASHBOARD_COLUMNS ||
    candidate.y + candidate.h > DASHBOARD_ROWS
  ) {
    return false;
  }
  return tiles.every(
    (tile) =>
      tile.tile_id === candidate.tile_id ||
      candidate.x + candidate.w <= tile.x ||
      tile.x + tile.w <= candidate.x ||
      candidate.y + candidate.h <= tile.y ||
      tile.y + tile.h <= candidate.y,
  );
}

export function firstFreePosition(
  tiles: StudioDashboardTile[],
  width = DEFAULT_TILE_WIDTH,
  height = DEFAULT_TILE_HEIGHT,
): { x: number; y: number } | null {
  for (let y = 0; y <= DASHBOARD_ROWS - height; y += 1) {
    for (let x = 0; x <= DASHBOARD_COLUMNS - width; x += 1) {
      if (
        canPlaceTile(tiles, {
          tile_id: "__new__",
          artifact_id: "",
          x,
          y,
          w: width,
          h: height,
        })
      ) {
        return { x, y };
      }
    }
  }
  return null;
}

export function tilePixelRect(
  tile: StudioDashboardTile,
  boardWidth: number,
  boardHeight: number,
  gap: number,
): { left: number; top: number; width: number; height: number } {
  const column = (boardWidth - gap * (DASHBOARD_COLUMNS - 1)) / DASHBOARD_COLUMNS;
  const row = (boardHeight - gap * (DASHBOARD_ROWS - 1)) / DASHBOARD_ROWS;
  return {
    left: tile.x * (column + gap),
    top: tile.y * (row + gap),
    width: tile.w * column + (tile.w - 1) * gap,
    height: tile.h * row + (tile.h - 1) * gap,
  };
}
