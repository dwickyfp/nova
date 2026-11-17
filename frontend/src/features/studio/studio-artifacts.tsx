import { useState } from "react";
import { DashboardEditor } from "@/features/native-dataviz/dashboard/editor";
import { DashboardHome } from "@/features/native-dataviz/dashboard/dashboard-home";

/**
 * Studio's Artifacts view: the native dashboards the person can open, in the
 * same editor as the Dataviz tab, so an artifact is one document in both places.
 */
export function StudioArtifacts({
  onSelectAgent,
}: {
  onSelectAgent: (id: string) => void;
}) {
  void onSelectAgent;
  const [open, setOpen] = useState<{ dashboardId: string; workspaceId: string } | null>(null);
  if (open) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <DashboardEditor
          key={open.dashboardId}
          dashboardId={open.dashboardId}
          workspaceId={open.workspaceId}
          onBack={() => setOpen(null)}
        />
      </div>
    );
  }
  return <DashboardHome onOpen={setOpen} />;
}
