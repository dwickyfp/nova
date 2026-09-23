# Nova Studio Dashboard

> Arrange saved Studio artifacts on a 6 column by 4 row canvas.

---

## Concept

Each dashboard belongs to one StarRocks user. It stores a title and the positions of artifact references. Query results and credentials are never stored in the dashboard. Opening a tile refreshes its artifact under the current user's StarRocks permissions.

## Operations

1. Open **Dashboard** in the Studio sidebar and create a named dashboard.
2. Drag a saved chart or table from the right panel onto the grid, or use its Add button to place it in the first available space.
3. Each new tile occupies 3 columns and 2 rows. Four default tiles fill the canvas.
4. In Edit mode, the grid fills the available designer area. Drag a tile header to move it. Drag its lower right corner to change its width and height by whole grid cells. The card becomes translucent with a red border while resizing and animates between cell sizes. Arrow keys also move or resize a focused tile.
5. Save the dashboard to enter View mode. View mode hides the grid and artifact panel. Select Edit to change the layout again.
6. The server rejects layouts outside the grid, overlapping tiles, and references to artifacts the user does not own.

## Nova UI

```text
┌─ Dashboard: Sales ──────────────────────────────────┬─ Artifacts ───────┐
│ [Save dashboard]                                    │ Search            │
│ ┌───────────────┬───────────────┐                   │ ↗ Revenue by day  │
│ │ Chart 3 × 2   │ Chart 3 × 2   │                   │ ↗ Orders by area  │
│ ├───────────────┼───────────────┤                   │ ▤ Detail table    │
│ │ Chart 3 × 2   │ Chart 3 × 2   │                   │                   │
│ └───────────────┴───────────────┘                   │                   │
└─────────────────────────────────────────────────────┴───────────────────┘
```

## Implementation Notes

Dashboard metadata is kept in `NOVA_SYSTEM.CONFIG_STUDIO_DASHBOARDS` as a Primary Key table. Its JSON layout contains artifact IDs and integer grid coordinates only. The Studio artifact refresh endpoint executes each saved query at view time with the current session and role. Dashboard create, update, and delete actions are audit logged.

## Limitations

The canvas is limited to 6 columns and 4 rows. Tiles cannot overlap. Dashboard sharing and scheduled refresh are outside this Studio feature.
