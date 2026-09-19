import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertCircle, CircleSlash } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines } from "@/components/ui/loading-overlay";
import type { GraphEdge, GraphNode } from "./api";
import {
  buildTaskFlow,
  viewportAboveInset,
  type TaskFlowNode,
} from "./task-flow-layout";
import { TaskNode } from "./task-node";

const NODE_TYPES = { task: TaskNode };

const FLOW_EDGE_OPTIONS = {
  markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
};

/**
 * Fits the graph into the area the drawer leaves visible.
 *
 * React Flow's own `fitView` centres on the full pane, which pushes the graph
 * behind the run-history drawer. Reserving the inset by shrinking the pane is
 * not an option (the flow must stay full-bleed), so the fit is computed for a
 * virtual viewport `bottomInset` pixels shorter and then shifted up. See
 * `viewportAboveInset` for the arithmetic.
 */
function fitAboveDrawer(
  instance: ReactFlowInstance<TaskFlowNode, Edge>,
  bottomInset: number,
) {
  instance.fitView({ padding: 0.2, maxZoom: 1 });
  instance.setViewport(viewportAboveInset(instance.getViewport(), bottomInset));
}

export function TaskFlow({
  nodes: graphNodes,
  edges: graphEdges,
  isLoading,
  isError,
  onRetry,
  bottomInset = 0,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  /** Pixels hidden behind the bottom drawer, so the fit can avoid them. */
  bottomInset?: number;
}) {
  const initial = useMemo(
    () => buildTaskFlow(graphNodes, graphEdges),
    [graphNodes, graphEdges],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initial.edges);
  const instanceRef = useRef<ReactFlowInstance<TaskFlowNode, Edge> | null>(
    null,
  );

  const runFit = useCallback((inset: number) => {
    const instance = instanceRef.current;
    if (instance) fitAboveDrawer(instance, inset);
  }, []);

  // The graph definition is refetched when the route changes; without this the
  // canvas would keep rendering the previous task's flow because `useNodesState`
  // captures its initial value only once.
  useEffect(() => {
    setNodes(initial.nodes);
    setEdges(initial.edges);
    // Re-centre after the nodes swap, using the current drawer inset.
    const frame = window.requestAnimationFrame(() => runFit(bottomInset));
    return () => window.cancelAnimationFrame(frame);
  }, [initial, setEdges, setNodes, bottomInset, runFit]);

  // Re-fit when the drawer is collapsed/expanded or the pane resizes.
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => runFit(bottomInset));
    return () => window.cancelAnimationFrame(frame);
  }, [bottomInset, runFit]);

  if (isLoading) {
    return (
      <div className="p-4">
        <LoadingLines rows={5} />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-4">
        <EmptyState
          variant="error"
          icon={AlertCircle}
          title="Could not load the task flow"
          description="The task API did not respond. Retry to load the graph definition."
          action={
            <Button variant="outline" size="sm" onClick={onRetry}>
              Retry
            </Button>
          }
        />
      </div>
    );
  }

  if (graphNodes.length === 0) {
    return (
      <div className="p-4">
        <EmptyState
          icon={CircleSlash}
          title="This task has no steps"
          description="The task definition holds no nodes. A task always has at least itself; a missing node means its definition is no longer readable."
        />
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onInit={(instance) => {
        instanceRef.current = instance;
        fitAboveDrawer(instance, bottomInset);
      }}
      nodeTypes={NODE_TYPES}
      defaultEdgeOptions={FLOW_EDGE_OPTIONS}
      minZoom={0.2}
      maxZoom={1.5}
      nodesConnectable={false}
      nodesDraggable
      elementsSelectable={false}
      proOptions={{ hideAttribution: true }}
      className="[&_.react-flow\_\_node]:!border-0"
    >
      <Background gap={16} size={1} />
      <Controls
        showInteractive={false}
        className="!top-3 !bottom-auto !left-3 [&>button]:!border-border [&>button]:!bg-background [&>button]:!fill-foreground"
      />
    </ReactFlow>
  );
}
