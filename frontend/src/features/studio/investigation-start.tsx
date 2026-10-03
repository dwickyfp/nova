import { useId, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { EmptyState } from "@/components/ui/empty-state";
import { api } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import { intelligenceApi, type Investigation, type MonitorConfiguration } from "@/features/intelligence/lifecycle-api";
import { workflowApi, type Mission } from "./workflow-api";

type Monitor = MonitorConfiguration & { id: string; revision: number };
type InvestigationResponse = { status: string; reason?: string | null; required_inputs?: string[]; investigation?: Investigation | null };

export function InvestigationStart({ mission, refresh }: { mission: Mission; refresh: () => void }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const id = useId();
  const [open, setOpen] = useState(false);
  const [monitorId, setMonitorId] = useState("");
  const [windows, setWindows] = useState({ baselineStart: "", baselineEnd: "", currentStart: "", currentEnd: "" });
  const operation = useRef<{ signature: string; id: string } | null>(null);
  const monitors = useQuery({ queryKey: ["studio", "workflow", epoch, mission.thread_id, "comparisons"], queryFn: () => intelligenceApi.page<Monitor>("monitors"), enabled: open, staleTime: 0, gcTime: 0, retry: false });
  const [more, setMore] = useState<Monitor[]>([]);
  const [after, setAfter] = useState<string | null>(null);
  const load = useMutation({ mutationFn: () => intelligenceApi.page<Monitor>("monitors", after ?? monitors.data?.next_after ?? ""), onSuccess: (page) => { setMore((current) => [...current, ...page.items]); setAfter(page.next_after); } });
  const rows = [...(monitors.data?.items ?? []), ...more];
  const chosen = rows.find((item) => item.id === monitorId);
  const start = useMutation({ mutationFn: async () => {
    if (!chosen) throw new Error("Select a governed metric comparison.");
    const configuration: MonitorConfiguration = {
      name: chosen.name, agent_id: chosen.agent_id, semantic: chosen.semantic, plan: chosen.plan,
      value_column: chosen.value_column, count_column: chosen.count_column, time_dimension: chosen.time_dimension,
      completeness_column: chosen.completeness_column, driver_dimensions: chosen.driver_dimensions,
      related_monitor_ids: chosen.related_monitor_ids, relative_threshold: chosen.relative_threshold,
      absolute_threshold: chosen.absolute_threshold, minimum_samples: chosen.minimum_samples,
      baseline_weeks: chosen.baseline_weeks, window_hours: chosen.window_hours, cooldown_hours: chosen.cooldown_hours,
      cadence_minutes: chosen.cadence_minutes, timezone: chosen.timezone, enabled: false,
    };
    const body = {
      configuration: { ...configuration, enabled: false },
      baseline_window: { start: new Date(windows.baselineStart).toISOString(), end: new Date(windows.baselineEnd).toISOString() },
      current_window: { start: new Date(windows.currentStart).toISOString(), end: new Date(windows.currentEnd).toISOString() },
    };
    const signature = JSON.stringify(body);
    if (operation.current?.signature !== signature) operation.current = { signature, id: crypto.randomUUID() };
    const result = await api.post<InvestigationResponse>("/intelligence/investigations/from-chat", { ...body, operation_id: operation.current.id });
    if (result.investigation) {
      const latest = await workflowApi.get(mission.mission_id);
      await workflowApi.link(latest, { kind: "investigation", id: result.investigation.id, revision: result.investigation.revision });
    }
    return result;
  }, onSuccess: refresh });
  const complete = chosen && Object.values(windows).every(Boolean);
  return <section aria-label="Start governed investigation" className="space-y-3">
    <Button variant="outline" className="min-h-11 w-full whitespace-normal" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{open ? "Close comparison inputs" : "Investigate a metric comparison"}</Button>
    {open && <form className="min-w-0 space-y-3" onSubmit={(event) => { event.preventDefault(); if (complete) start.mutate(); }}>
      <p className="text-xs text-muted-foreground">Choose a governed metric definition and complete historical windows of equal length. This investigation does not create a periodic schedule.</p>
      {monitors.isPending ? <LoadingLines rows={1} /> : monitors.isError ? <EmptyState variant="error" title="Metric comparisons unavailable" description={monitors.error.message} action={<Button type="button" variant="outline" onClick={() => void monitors.refetch()}>Retry</Button>} /> : rows.length ? <>
        <div className="space-y-2"><Label htmlFor={`${id}-metric`}>Metric comparison</Label><select id={`${id}-metric`} className="min-h-11 w-full min-w-0 rounded-md border bg-card px-2 text-sm" value={monitorId} onChange={(event) => setMonitorId(event.target.value)} required>
          <option value="">Choose a governed comparison</option>{rows.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.value_column}</option>)}
        </select></div>
        {(after !== null || !load.isSuccess) && (after ?? monitors.data?.next_after) && <Button type="button" variant="ghost" className="min-h-11" disabled={load.isPending} onClick={() => load.mutate()}>Load more comparisons</Button>}
      </> : <p className="text-xs text-muted-foreground">No governed metric comparison is available. Configure a monitor in News, then return here.</p>}
      {load.isError && <p role="alert" className="text-xs text-destructive">{load.error.message}</p>}
      {([ ["baselineStart", "Baseline start"], ["baselineEnd", "Baseline end"], ["currentStart", "Observed start"], ["currentEnd", "Observed end"] ] as const).map(([key, label]) => <div key={key} className="min-w-0 space-y-2">
        <Label htmlFor={`${id}-${key}`}>{label}</Label><Input id={`${id}-${key}`} type="datetime-local" value={windows[key]} required onChange={(event) => setWindows((current) => ({ ...current, [key]: event.target.value }))} className="min-h-11 min-w-0 max-w-full" />
      </div>)}
      <Button type="submit" className="min-h-11" disabled={!complete || start.isPending}>{start.isPending ? "Investigating…" : "Run governed investigation"}</Button>
      {start.isError && <p role="alert" className="break-words text-xs text-destructive">{start.error.message}</p>}
      {start.isSuccess && <p role="status" className="break-words text-xs">{start.data.status === "clarification" ? `Comparison inputs required: ${start.data.required_inputs?.join(", ") ?? start.data.reason}.` : start.data.status === "insufficient" ? `Evidence is incomplete: ${start.data.reason?.split("_").join(" ") ?? "insufficient observations"}.` : "Investigation recorded and linked to this mission."}</p>}
    </form>}
  </section>;
}
