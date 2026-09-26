import { useCallback, useEffect, useMemo } from "react";
import {
  Background,
  Controls,
  ControlButton,
  MarkerType,
  ReactFlow,
  Panel,
  useEdgesState,
  useNodesState,
  useNodesInitialized,
  useReactFlow,
  useStore,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertCircle, CircleSlash, Maximize } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines } from "@/components/ui/loading-overlay";
import type { GraphEdge, GraphNode } from "./api";
import {
  buildTaskFlow,
  taskFlowViewport,
  type TaskFlowNode,
} from "./task-flow-layout";
import { TaskNode } from "./task-node";
import { TaskSQLDialog } from "./task-sql-dialog";

const NODE_TYPES = { task: TaskNode };

const FLOW_EDGE_OPTIONS = {
  markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
};

function FlowControls({ bottomInset }: { bottomInset: number }) {
  const { getNodes, getNodesBounds, setViewport } = useReactFlow<
    TaskFlowNode,
    Edge
  >();
  const initialized = useNodesInitialized();
  const width = useStore((state) => state.width);
  const height = useStore((state) => state.height);
  const fit = useCallback(() => {
    if (!initialized || !width || !height) return;
    void setViewport(
      taskFlowViewport(getNodesBounds(getNodes()), width, height, bottomInset),
    );
  }, [
    initialized,
    width,
    height,
    bottomInset,
    getNodes,
    getNodesBounds,
    setViewport,
  ]);

  useEffect(() => {
    fit();
  }, [fit]);

  return (
    <Controls
      showInteractive={false}
      showFitView={false}
      className="!top-3 !bottom-auto !left-3 [&>button]:!border-border [&>button]:!bg-background [&>button]:!fill-foreground"
    >
      <ControlButton onClick={fit} title="Fit view" aria-label="Fit view">
        <Maximize />
      </ControlButton>
    </Controls>
  );
}

export function TaskFlow({
  graphId,
  nodes: graphNodes,
  edges: graphEdges,
  isLoading,
  isError,
  onRetry,
  bottomInset = 0,
}: {
  graphId: string;
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
  const selected = graphNodes.find((node) =>
    nodes.some((flowNode) => flowNode.id === node.name && flowNode.selected),
  );

  // The graph definition is refetched when the route changes; without this the
  // canvas would keep rendering the previous task's flow because `useNodesState`
  // captures its initial value only once.
  useEffect(() => {
    setNodes((current) =>
      initial.nodes.map((node) => ({
        ...node,
        selected: current.find((existing) => existing.id === node.id)?.selected,
      })),
    );
    setEdges(initial.edges);
  }, [initial, setEdges, setNodes]);

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
      nodeTypes={NODE_TYPES}
      defaultEdgeOptions={FLOW_EDGE_OPTIONS}
      minZoom={0.01}
      maxZoom={1.5}
      nodesConnectable={false}
      nodesDraggable
      elementsSelectable
      edgesFocusable={false}
      deleteKeyCode={null}
      multiSelectionKeyCode={null}
      proOptions={{ hideAttribution: true }}
      className="[&_.react-flow\_\_node]:!border-0"
    >
      <Background gap={16} size={1} />
      <FlowControls bottomInset={bottomInset} />
      {selected ? (
        <Panel position="top-right">
          <TaskSQLDialog
            key={selected.task_id}
            graphId={graphId}
            node={selected}
          />
        </Panel>
      ) : null}
    </ReactFlow>
  );
}
