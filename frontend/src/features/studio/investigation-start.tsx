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
import { semanticViewsApi } from "@/features/intelligence/semantic-views-api";

type Monitor = MonitorConfiguration & { id: string; revision: number };
type InvestigationResponse = { status: string; reason?: string | null; required_inputs?: string[]; investigation?: Investigation | null };

export function InvestigationStart({ mission, refresh }: { mission: Mission; refresh: () => void }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const id = useId();
  const established = mission.investigation_requirements?.established;
  const localInput = (value?: string) => {
    if (!value) return "";
    const date = new Date(value);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 23);
  };
  const [open, setOpen] = useState(false);
  const [monitorId, setMonitorId] = useState("");
  const [windows, setWindows] = useState({
    baselineStart: localInput(established?.baseline_window?.start),
    baselineEnd: localInput(established?.baseline_window?.end),
    currentStart: localInput(established?.current_window?.start),
    currentEnd: localInput(established?.current_window?.end),
  });
  const [timeDimension, setTimeDimension] = useState("");
  const operation = useRef<{ signature: string; id: string } | null>(null);
  const monitors = useQuery({ queryKey: ["studio", "workflow", epoch, mission.thread_id, "comparisons"], queryFn: () => intelligenceApi.page<Monitor>("monitors"), enabled: open, staleTime: 0, gcTime: 0, retry: false });
  const [more, setMore] = useState<Monitor[]>([]);
  const [after, setAfter] = useState<string | null>(null);
  const load = useMutation({ mutationFn: () => intelligenceApi.page<Monitor>("monitors", after ?? monitors.data?.next_after ?? ""), onSuccess: (page) => { setMore((current) => [...current, ...page.items]); setAfter(page.next_after); } });
  const rows = [...(monitors.data?.items ?? []), ...more];
  const exact = established?.semantic;
  const compatible = exact ? rows.filter((item) => item.semantic.view_id === exact.view_id && item.semantic.version === exact.version && item.semantic.fingerprint === exact.fingerprint && established.metrics.includes(item.value_column)) : rows;
  const chosen = compatible.find((item) => item.id === monitorId) ?? (compatible.length === 1 ? compatible[0] : undefined);
  const semantic = useQuery({ queryKey: ["studio", "workflow", epoch, mission.thread_id, "setup-semantic", exact], queryFn: () => semanticViewsApi.get(exact!.view_id), enabled: open && Boolean(exact), staleTime: 0, gcTime: 0, retry: false });
  const definition = semantic.data?.versions.find((version) => version.version === exact?.version && version.fingerprint === exact.fingerprint)?.definition;
  const metric = definition?.metrics?.find((item) => item.name === established?.metrics[0]);
  const timeFields = definition?.datasets?.flatMap((dataset) => (dataset.fields ?? []).filter((field) => field.dimension?.is_time).map((field) => `${dataset.name}.${field.name}`)) ?? [];
  const selectedTime = timeDimension || metric?.default_time_dimension || (timeFields.length === 1 ? timeFields[0] : "");
  const derived: MonitorConfiguration | undefined = exact && established?.metrics.length === 1 && selectedTime && !established.filter_shape.length && !established.named_filters.length ? {
    name: `One-time ${established.metrics[0]}`, agent_id: mission.agent_id ?? "", semantic: exact,
    plan: { metrics: established.metrics, dimensions: [], filters: [], named_filters: [] },
    value_column: established.metrics[0], count_column: null, time_dimension: selectedTime,
    driver_dimensions: established.dimensions.slice(0, 3), timezone: established.timezone ?? "Asia/Jakarta", enabled: false,
  } : undefined;
  const start = useMutation({ mutationFn: async () => {
    if (!chosen && !derived?.agent_id) throw new Error("Select a governed metric comparison.");
    const configuration: MonitorConfiguration = chosen ? {
      name: chosen.name, agent_id: chosen.agent_id, semantic: chosen.semantic, plan: chosen.plan,
      value_column: chosen.value_column, count_column: chosen.count_column, time_dimension: chosen.time_dimension,
      completeness_column: chosen.completeness_column, driver_dimensions: chosen.driver_dimensions,
      related_monitor_ids: chosen.related_monitor_ids, relative_threshold: chosen.relative_threshold,
      absolute_threshold: chosen.absolute_threshold, minimum_samples: chosen.minimum_samples,
      baseline_weeks: chosen.baseline_weeks, window_hours: chosen.window_hours, cooldown_hours: chosen.cooldown_hours,
      cadence_minutes: chosen.cadence_minutes, timezone: chosen.timezone, enabled: false,
    } : derived!;
    const body = {
      configuration: { ...configuration, enabled: false },
      baseline_window: { start: new Date(windows.baselineStart).toISOString(), end: new Date(windows.baselineEnd).toISOString() },
      current_window: { start: new Date(windows.currentStart).toISOString(), end: new Date(windows.currentEnd).toISOString() },
      ...(established?.timezone ? { calendar_timezone: established.timezone } : {}),
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
  const complete = (chosen || derived?.agent_id) && Object.values(windows).every(Boolean);
  return <section aria-label="Start governed investigation" className="space-y-3">
    <Button variant="outline" className="min-h-11 w-full whitespace-normal" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{open ? "Close comparison inputs" : mission.investigation_requirements ? "Complete investigation setup" : "Investigate a metric comparison"}</Button>
    {open && <form className="min-w-0 space-y-3" onSubmit={(event) => { event.preventDefault(); if (complete) start.mutate(); }}>
      <p className="text-xs text-muted-foreground">Complete the missing comparison inputs. Windows compare the same elapsed calendar span. This creates a one-time investigation.</p>
      {established && <p className="break-words text-xs">Established: {established.metrics.join(", ")} · semantic version {established.semantic?.version}. Missing: {mission.investigation_requirements?.required_inputs.map((value) => value.split("_").join(" ")).join(", ")}.</p>}
      {monitors.isPending ? <LoadingLines rows={1} /> : monitors.isError ? <EmptyState variant="error" title="Metric comparisons unavailable" description={monitors.error.message} action={<Button type="button" variant="outline" onClick={() => void monitors.refetch()}>Retry</Button>} /> : compatible.length ? <>
        <div className="space-y-2"><Label htmlFor={`${id}-metric`}>Metric comparison</Label><select id={`${id}-metric`} className="min-h-11 w-full min-w-0 rounded-md border bg-card px-2 text-sm" value={chosen?.id ?? ""} onChange={(event) => setMonitorId(event.target.value)} required>
          <option value="">Choose a governed comparison</option>{compatible.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.value_column}</option>)}
        </select></div>
        {(after !== null || !load.isSuccess) && (after ?? monitors.data?.next_after) && <Button type="button" variant="ghost" className="min-h-11" disabled={load.isPending} onClick={() => load.mutate()}>Load more comparisons</Button>}
      </> : exact ? <div className="space-y-2"><Label htmlFor={`${id}-time`}>Time dimension</Label><select id={`${id}-time`} className="min-h-11 w-full min-w-0 rounded-md border bg-card px-2 text-sm" value={selectedTime} onChange={(event) => setTimeDimension(event.target.value)}><option value="">Choose a governed time dimension</option>{timeFields.map((field) => <option key={field} value={field}>{field}</option>)}</select><p className="text-xs text-muted-foreground">Sample count remains unknown for this one-time comparison.</p></div> : <p className="text-xs text-muted-foreground">Select a published semantic metric in chat to establish the comparison.</p>}
      {load.isError && <p role="alert" className="text-xs text-destructive">{load.error.message}</p>}
      {([ ["baselineStart", "Baseline start"], ["baselineEnd", "Baseline end"], ["currentStart", "Observed start"], ["currentEnd", "Observed end"] ] as const).map(([key, label]) => <div key={key} className="min-w-0 space-y-2">
        <Label htmlFor={`${id}-${key}`}>{label}</Label><Input id={`${id}-${key}`} type="datetime-local" step="0.001" value={windows[key]} required onChange={(event) => setWindows((current) => ({ ...current, [key]: event.target.value }))} className="min-h-11 min-w-0 max-w-full" />
      </div>)}
      <Button type="submit" className="min-h-11" disabled={!complete || start.isPending}>{start.isPending ? "Investigating…" : "Run governed investigation"}</Button>
      {start.isError && <p role="alert" className="break-words text-xs text-destructive">{start.error.message}</p>}
      {start.isSuccess && <p role="status" className="break-words text-xs">{start.data.status === "clarification" ? `Comparison inputs required: ${start.data.required_inputs?.join(", ") ?? start.data.reason}.` : start.data.status === "insufficient" ? `Evidence is incomplete: ${start.data.reason?.split("_").join(" ") ?? "insufficient observations"}.` : "Investigation recorded and linked to this mission."}</p>}
    </form>}
  </section>;
}
