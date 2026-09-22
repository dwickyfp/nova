import { createFileRoute } from "@tanstack/react-router";
import { AccessControlPage } from "@/features/access-control";

export const Route = createFileRoute("/_authenticated/access-control")({
  component: AccessControlPage,
});
