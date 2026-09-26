import { Handle, Position, type NodeProps } from "@xyflow/react";
import { cn } from "@/lib/utils";
import type { StatusTone } from "@/components/ui/status-badge";
import type { TaskFlowNode, TaskNodeData } from "./task-flow-layout";

const HANDLE_CLASS = "!size-2 !border-border !bg-background";

/**
 * One task in the flow. Renders the task name, its schedule, and its last
 * observed state. Selecting a node exposes its SQL in the canvas toolbar.
 */
export function TaskNode({ data, selected }: NodeProps<TaskFlowNode>) {
  const nodeData = data as TaskNodeData;
  return (
    <div
      className={cn(
        "flex w-[200px] flex-col gap-1 rounded-lg border bg-card px-3 py-2 text-left shadow-sm",
        nodeData.isFinalizer
          ? "border-primary/40 border-dashed"
          : "border-border",
        selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
      )}
    >
      <Handle type="target" position={Position.Left} className={HANDLE_CLASS} />
      <span className="truncate text-xs font-medium" title={nodeData.label}>
        {nodeData.label}
      </span>
      <span className="truncate text-[11px] text-muted-foreground">
        {nodeData.schedule}
      </span>
      <StateDot tone={nodeData.tone} state={nodeData.lastState} />
      <Handle
        type="source"
        position={Position.Right}
        className={HANDLE_CLASS}
      />
    </div>
  );
}

function StateDot({ tone, state }: { tone: StatusTone; state: string | null }) {
  const toneClass: Record<StatusTone, string> = {
    success: "bg-success",
    warning: "bg-warning",
    info: "bg-info",
    danger: "bg-destructive",
    neutral: "bg-muted-foreground/50",
    primary: "bg-primary",
  };
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
      <span
        aria-hidden="true"
        className={cn("size-1.5 shrink-0 rounded-full", toneClass[tone])}
      />
      {state ?? "no runs"}
    </span>
  );
}
