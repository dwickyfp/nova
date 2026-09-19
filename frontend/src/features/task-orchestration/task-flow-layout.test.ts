import { describe, expect, it } from "vitest";
import type { GraphEdge, GraphNode } from "./api";
import {
  buildTaskFlow,
  NODE_HEIGHT,
  NODE_WIDTH,
  viewportAboveInset,
} from "./task-flow-layout";

function node(name: string, overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    name,
    task_id: `id_${name}`,
    schedule_kind: "manual",
    schedule_expr: null,
    timezone: "UTC",
    overlap_policy: "skip",
    when_expr: null,
    created_by: "alice",
    is_finalizer: false,
    last_state: null,
    ...overrides,
  };
}

function edge(
  parent: string,
  child: string,
  edge_kind: "after" | "finalize" = "after",
): GraphEdge {
  return { parent_task: parent, child_task: child, edge_kind };
}

describe("buildTaskFlow", () => {
  it("places a child one column to the right of its parent", () => {
    const { nodes } = buildTaskFlow([node("a"), node("b")], [edge("a", "b")]);

    const a = nodes.find((n) => n.id === "a")!;
    const b = nodes.find((n) => n.id === "b")!;
    expect(a.position.x).toBe(0);
    expect(b.position.x).toBe(NODE_WIDTH + 72);
    expect(a.position.y).toBe(b.position.y);
  });

  it("ranks a diamond by depth, not insertion order", () => {
    // a -> b -> d, a -> c -> d: d must sit right of both b and c.
    const { nodes } = buildTaskFlow(
      [node("d"), node("a"), node("b"), node("c")],
      [edge("a", "b"), edge("a", "c"), edge("b", "d"), edge("c", "d")],
    );

    const rankOf = (name: string) =>
      nodes.find((n) => n.id === name)!.position.x;
    expect(rankOf("a")).toBe(0);
    expect(rankOf("b")).toBe(rankOf("c"));
    expect(rankOf("d")).toBeGreaterThan(rankOf("b"));
  });

  it("stacks siblings in separate rows", () => {
    const { nodes } = buildTaskFlow(
      [node("a"), node("b"), node("c")],
      [edge("a", "b"), edge("a", "c")],
    );

    const ys = ["b", "c"].map(
      (name) => nodes.find((n) => n.id === name)!.position.y,
    );
    expect(new Set(ys).size).toBe(2);
    expect(Math.abs(ys[0] - ys[1])).toBeGreaterThanOrEqual(NODE_HEIGHT);
  });

  it("renders a finalizer to the right of its target without ranking the target", () => {
    const { nodes, edges } = buildTaskFlow(
      [node("a"), node("f", { is_finalizer: true })],
      [edge("a", "f", "finalize")],
    );

    const a = nodes.find((n) => n.id === "a")!;
    const f = nodes.find((n) => n.id === "f")!;
    expect(a.position.x).toBe(0);
    expect(f.position.x).toBeGreaterThan(a.position.x);
    expect(f.data.isFinalizer).toBe(true);

    const finalizerEdge = edges.find(
      (e) => e.source === "a" && e.target === "f",
    )!;
    expect(finalizerEdge.style?.strokeDasharray).toBeTruthy();
    expect(finalizerEdge.label).toBe("FINALIZE");
  });

  it("does not rank a downstream task behind a finalizer edge", () => {
    // a -> b (dependency); a -finalize-> f. The finalizer must not push f's
    // rank onto anything else, and b must still be at rank 1.
    const { nodes } = buildTaskFlow(
      [node("a"), node("b"), node("f", { is_finalizer: true })],
      [edge("a", "b"), edge("a", "f", "finalize")],
    );

    const rankOf = (name: string) =>
      nodes.find((n) => n.id === name)!.position.x;
    expect(rankOf("b")).toBe(NODE_WIDTH + 72);
    expect(rankOf("f")).toBe(NODE_WIDTH + 72);
  });

  it("terminates on a cycle instead of overflowing the stack", () => {
    const { nodes } = buildTaskFlow(
      [node("a"), node("b")],
      [edge("a", "b"), edge("b", "a")],
    );

    expect(nodes).toHaveLength(2);
    for (const n of nodes) {
      expect(Number.isFinite(n.position.x)).toBe(true);
      expect(Number.isFinite(n.position.y)).toBe(true);
    }
  });

  it("drops an edge whose endpoint is not a known node", () => {
    const { edges } = buildTaskFlow([node("a")], [edge("a", "ghost")]);

    expect(edges).toHaveLength(0);
  });

  it("labels a plain dependency as AFTER by omission, not a mislabel", () => {
    const { edges } = buildTaskFlow([node("a"), node("b")], [edge("a", "b")]);

    expect(edges[0].label).toBeUndefined();
    expect(edges[0].style?.strokeDasharray).toBeUndefined();
  });
});

describe("viewportAboveInset", () => {
  const viewport = { x: 10, y: 100, zoom: 1 };

  it("shifts the viewport up by half the hidden band, scaled by zoom", () => {
    // The visible box is the pane minus a 300px drawer, so its centre sits 150px
    // above the pane centre; in flow coordinates that is 150 * zoom.
    expect(viewportAboveInset(viewport, 300)).toEqual({
      x: 10,
      y: 250,
      zoom: 1,
    });
  });

  it("scales the shift with the zoom level", () => {
    expect(viewportAboveInset({ x: 0, y: 0, zoom: 2 }, 200)).toEqual({
      x: 0,
      y: 200,
      zoom: 2,
    });
  });

  it("leaves the viewport untouched when the drawer is collapsed", () => {
    expect(viewportAboveInset(viewport, 0)).toEqual(viewport);
    expect(viewportAboveInset(viewport, -5)).toEqual(viewport);
  });
});
