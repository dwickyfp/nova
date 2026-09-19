import { createFileRoute } from "@tanstack/react-router";
import { TaskDetail } from "@/features/task-orchestration";

export const Route = createFileRoute("/_authenticated/tasks/$graphId")({
  component: RouteComponent,
});

function RouteComponent() {
  const { graphId } = Route.useParams();
  return <TaskDetail graphId={graphId} />;
}
