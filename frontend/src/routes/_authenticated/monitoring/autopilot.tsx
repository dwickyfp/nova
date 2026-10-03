import { createFileRoute } from "@tanstack/react-router";
import { QueryAutopilot } from "@/features/monitoring/autopilot";
export const Route = createFileRoute("/_authenticated/monitoring/autopilot")({
  component: QueryAutopilot,
});
