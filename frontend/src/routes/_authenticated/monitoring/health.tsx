import { createFileRoute } from "@tanstack/react-router";
import { MonitoringHealth } from "@/features/monitoring/health";

export const Route = createFileRoute("/_authenticated/monitoring/health")({
  component: MonitoringHealth,
});
