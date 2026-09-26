import {
  getViewportForBounds,
  type Edge,
  type Node,
  type Rect,
} from "@xyflow/react";
import type { GraphEdge, GraphNode, ScheduleKind, TaskRunState } from "./api";
import { formatSchedule, type ToneName } from "./presentation";

/** Dagre-free layered layout: parent ranks left, child ranks right. */

export type TaskNodeData = {
  label: string;
  schedule: string;
  isFinalizer: boolean;
  lastState: TaskRunState | null;
  tone: ToneName;
  [key: string]: unknown;
};

export type TaskFlowNode = Node<TaskNodeData, "task">;

export const NODE_WIDTH = 200;
export const NODE_HEIGHT = 74;
const H_GAP = 72;
const V_GAP = 32;

const DEFAULT_POSITION = { x: 0, y: 0 };

/**
 * Assign each node a rank: 0 for a node with no incoming dependency edge, then
 * one past its deepest parent. Finalizer edges are excluded from the ranking —
 * a finalizer runs *after* the graph completes, so it must not push its target
 * to a later rank — but the finalizer node itself still gets placed after its
 * target.
 *
 * Cycles (which a hand-edited graph could contain) are broken by ignoring an
 * edge back to an already-ranked node, so layout always terminates.
 */
function rankNodes(
  nodes: GraphNode[],
  edges: GraphEdge[],
): Map<string, number> {
  const names = new Set(nodes.map((node) => node.name));
  const dependencies = edges.filter((edge) => edge.edge_kind !== "finalize");
  const ranks = new Map<string, number>();
  const byName = new Map(nodes.map((node) => [node.name, node]));

  const parents = new Map<string, string[]>();
  for (const edge of dependencies) {
    if (!names.has(edge.parent_task) || !names.has(edge.child_task)) continue;
    const list = parents.get(edge.child_task) ?? [];
    list.push(edge.parent_task);
    parents.set(edge.child_task, list);
  }

  const visiting = new Set<string>();
  function rank(name: string): number {
    const cached = ranks.get(name);
    if (cached !== undefined) return cached;
    if (visiting.has(name)) return 0; // cycle guard
    visiting.add(name);
    const ownParents = parents.get(name) ?? [];
    const value = ownParents.length
      ? Math.max(...ownParents.map((parent) => rank(parent) + 1))
      : 0;
    visiting.delete(name);
    ranks.set(name, value);
    return value;
  }
  for (const node of byName.keys()) rank(node);

  // A finalizer is not a dependency of its target, but it should sit to the
  // right of it rather than floating at rank 0.
  for (const edge of edges) {
    if (edge.edge_kind !== "finalize") continue;
    if (!names.has(edge.parent_task) || !names.has(edge.child_task)) continue;
    const targetRank = ranks.get(edge.parent_task) ?? 0;
    ranks.set(
      edge.child_task,
      Math.max(ranks.get(edge.child_task) ?? 0, targetRank + 1),
    );
  }
  return ranks;
}

export function buildTaskFlow(
  nodes: GraphNode[],
  edges: GraphEdge[],
): { nodes: TaskFlowNode[]; edges: Edge[] } {
  const ranks = rankNodes(nodes, edges);
  const columns = new Map<number, string[]>();
  for (const node of nodes) {
    const rank = ranks.get(node.name) ?? 0;
    const column = columns.get(rank) ?? [];
    column.push(node.name);
    columns.set(rank, column);
  }
  for (const column of columns.values()) column.sort();

  const positionOf = new Map<string, { x: number; y: number }>();
  for (const [rank, column] of columns) {
    column.forEach((name, index) => {
      positionOf.set(name, {
        x: rank * (NODE_WIDTH + H_GAP),
        y: index * (NODE_HEIGHT + V_GAP),
      });
    });
  }

  const flowNodes: TaskFlowNode[] = nodes.map((node) => ({
    id: node.name,
    type: "task" as const,
    position: positionOf.get(node.name) ?? DEFAULT_POSITION,
    data: {
      label: node.is_finalizer ? `${node.name} (finalizer)` : node.name,
      schedule: formatSchedule(
        node.schedule_kind as ScheduleKind,
        node.schedule_expr,
      ),
      isFinalizer: node.is_finalizer,
      lastState: node.last_state,
      tone: taskNodeTone(node),
    },
    sourcePosition: "LR" as never,
    targetPosition: "LR" as never,
  }));

  const flowEdges: Edge[] = [];
  for (const edge of edges) {
    const known =
      positionOf.has(edge.parent_task) && positionOf.has(edge.child_task);
    if (!known) continue;
    const isFinalizer = edge.edge_kind === "finalize";
    flowEdges.push({
      id: `${edge.parent_task}->${edge.child_task}:${edge.edge_kind}`,
      source: edge.parent_task,
      target: edge.child_task,
      animated: false,
      style: isFinalizer ? { strokeDasharray: "6 4" } : undefined,
      label: isFinalizer ? "FINALIZE" : undefined,
      labelStyle: { fontSize: 10 },
      labelBgStyle: { fill: "transparent" },
      labelBgPadding: [2, 4] as [number, number],
    });
  }

  return { nodes: flowNodes, edges: flowEdges };
}

function taskNodeTone(node: GraphNode): ToneName {
  if (node.is_finalizer) return "primary";
  switch (node.last_state) {
    case "success":
      return "success";
    case "failed":
      return "danger";
    case "running":
      return "info";
    case "abandoned":
      return "warning";
    default:
      return "neutral";
  }
}

/** Fit to the visible canvas; the full-bleed background stays behind the drawer. */
export function taskFlowViewport(
  bounds: Rect,
  width: number,
  height: number,
  bottomInset: number,
): { x: number; y: number; zoom: number } {
  return getViewportForBounds(
    bounds,
    width,
    Math.max(1, height - Math.max(0, bottomInset)),
    0.01,
    1,
    0.2,
  );
}
