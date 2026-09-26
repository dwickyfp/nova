import { createFileRoute } from "@tanstack/react-router";
import { MonitoringAI } from "@/features/monitoring/ai";

export const Route = createFileRoute("/_authenticated/monitoring/ai")({
  component: MonitoringAI,
});
