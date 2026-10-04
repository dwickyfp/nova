import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";
import { mergeMissionProjection, workflowApi, type ResumableMission } from "./workflow-api";

export type MissionTurnChoice = { mode: "automatic" | "new" | "continue"; missionId?: string };

export function MissionTurnControls({ threadId, value, onChange, disabled }: {
  threadId: string;
  value: MissionTurnChoice;
  onChange: (value: MissionTurnChoice) => void;
  disabled: boolean;
}) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const client = useQueryClient();
  const operation = useRef<{ signature: string; id: string } | null>(null);
  const key = ["studio", "workflow", epoch, threadId];
  const current = useQuery({ queryKey: key, queryFn: ({ signal }) => workflowApi.list(threadId, signal), retry: false, staleTime: 0, gcTime: 0 });
  const summaries = useQuery({ queryKey: [...key, "resumable"], queryFn: ({ signal }) => workflowApi.resumable(threadId, signal), retry: false, staleTime: 0, gcTime: 0 });
  const rows = current.data?.missions.filter((mission) => !mission.cancel_requested) ?? [];
  const selected = rows.find((mission) => mission.mission_id === value.missionId) ?? rows[0];
  const resume = useMutation({
    mutationFn: (mission: ResumableMission) => {
      const signature = `${mission.mission_id}:${mission.revision}`;
      if (operation.current?.signature !== signature) operation.current = { signature, id: crypto.randomUUID() };
      return workflowApi.resume(mission, operation.current.id);
    },
    onSuccess: (mission) => {
      client.setQueryData(key, (previous: Parameters<typeof mergeMissionProjection>[0]) => mergeMissionProjection(previous, mission, threadId));
      void client.invalidateQueries({ queryKey: [...key, "resumable"] });
      onChange({ mode: "continue", missionId: mission.mission_id });
    },
  });
  const historical = summaries.data?.missions.filter((mission) => mission.resume_required) ?? [];
  return <section aria-label="Mission turn controls" className="mx-auto mb-2 w-full max-w-3xl space-y-2">
    <div className="flex flex-wrap items-center gap-1" aria-label="Next message mission">
      <Button size="sm" variant={value.mode === "automatic" ? "secondary" : "ghost"} className="min-h-11" aria-pressed={value.mode === "automatic"} disabled={disabled} onClick={() => onChange({ mode: "automatic" })}>Automatic</Button>
      <Button size="sm" variant={value.mode === "continue" ? "secondary" : "ghost"} className="min-h-11" aria-pressed={value.mode === "continue"} disabled={disabled || !selected} onClick={() => selected && onChange({ mode: "continue", missionId: selected.mission_id })}>Continue mission</Button>
      <Button size="sm" variant={value.mode === "new" ? "secondary" : "ghost"} className="min-h-11" aria-pressed={value.mode === "new"} disabled={disabled} onClick={() => onChange({ mode: "new" })}>New mission</Button>
    </div>
    {value.mode === "continue" && selected && <label className="block text-xs text-muted-foreground">Continue
      <select aria-label="Mission to continue" className="mt-1 min-h-11 w-full rounded-md border bg-card px-2 text-sm text-foreground" value={selected.mission_id} disabled={disabled} onChange={(event) => onChange({ mode: "continue", missionId: event.target.value })}>
        {rows.map((mission) => <option key={mission.mission_id} value={mission.mission_id}>{mission.objective}</option>)}
      </select>
    </label>}
    {historical.length > 0 && <div className="space-y-2 text-xs">
      <p className="text-muted-foreground">Resume previous work after checking current access. New execution requires current consent.</p>
      {historical.map((mission) => <div key={mission.mission_id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2">
        <span className="min-w-0 flex-1 break-words">{mission.objective}</span>
        <Button size="sm" variant="outline" className="min-h-11" disabled={disabled || resume.isPending} onClick={() => resume.mutate(mission)}>{resume.isPending && resume.variables?.mission_id === mission.mission_id ? "Checking access…" : "Resume mission"}</Button>
      </div>)}
    </div>}
    {summaries.isError && <p role="alert" className="text-xs text-destructive">Previous missions unavailable: {summaries.error.message}</p>}
    {resume.isError && <p role="alert" className="text-xs text-destructive">{resume.error.message}</p>}
    {resume.isSuccess && <p role="status" className="text-xs text-muted-foreground">Mission resumed with current access.</p>}
  </section>;
}
