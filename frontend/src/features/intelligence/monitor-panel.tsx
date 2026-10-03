import { useId, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import { agentsApi } from "@/features/agents/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { semanticViewsApi } from "./semantic-views-api";
import { intelligenceApi, type Page, type SemanticRef } from "./lifecycle-api";

type Configuration = {
  name: string;
  agent_id: string;
  semantic: SemanticRef;
  plan: Record<string, unknown>;
  value_column: string;
  count_column: string;
  time_dimension: string;
  completeness_column?: string | null;
  driver_dimensions: string[];
  related_monitor_ids: string[];
  relative_threshold: number;
  absolute_threshold: number;
  minimum_samples: number;
  baseline_weeks: number;
  window_hours: number;
  cooldown_hours: number;
  cadence_minutes: number;
  timezone: string;
  enabled: boolean;
};
type Monitor = Configuration & { id: string; revision: number };

export function MonitorPanel({ onNews }: { onNews: (id: string) => void }) {
  const epoch = useAuthStore((s) => s.securityEpoch);
  return <Monitors key={epoch} epoch={epoch} onNews={onNews} />;
}

function Monitors({
  epoch,
  onNews,
}: {
  epoch: number;
  onNews: (id: string) => void;
}) {
  const id = useId();
  const [agentId, setAgentId] = useState("");
  const [viewId, setViewId] = useState("");
  const [name, setName] = useState("");
  const [metric, setMetric] = useState("");
  const [count, setCount] = useState("");
  const [time, setTime] = useState("");
  const [drivers, setDrivers] = useState("");
  const [complete, setComplete] = useState("");
  const [threshold, setThreshold] = useState("10");
  const [minimum, setMinimum] = useState("30");
  const [hours, setHours] = useState("24");
  const [cadence, setCadence] = useState("15");
  const [timezone, setTimezone] = useState("Asia/Jakarta");
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const operation = useRef({ signature: "", id: "" });
  const config = { staleTime: 0, gcTime: 0, retry: false as const };
  const agents = useQuery({
    ...config,
    queryKey: ["monitor-agents", epoch],
    queryFn: agentsApi.list,
  });
  const agent = agents.data?.agents.find((a) => a.agent_id === agentId);
  const activeView = viewId || agent?.semantic_view_ids?.[0] || "";
  const view = useQuery({
    ...config,
    queryKey: ["monitor-view", epoch, activeView],
    queryFn: () => semanticViewsApi.get(activeView),
    enabled: Boolean(activeView),
  });
  const version =
    !view.isFetching && !view.isError
      ? view.data?.versions.find((v) => v.version === view.data.active_version)
      : undefined;
  const metricNames = version?.definition.metrics?.map((m) => m.name) ?? [];
  const dimensions =
    version?.definition.datasets?.flatMap(
      (d) => d.fields?.map((f) => `${d.name}.${f.name}`) ?? [],
    ) ?? [];
  const monitors = useInfiniteQuery({
    ...config,
    queryKey: ["monitors", epoch],
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      intelligenceApi.page<Monitor>("monitors", pageParam),
    getNextPageParam: (page: Page<Monitor>) => page.next_after || undefined,
  });

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "The monitor could not be updated.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    if (!version) return;
    const configuration: Configuration = {
      name,
      agent_id: agentId,
      semantic: {
        view_id: activeView,
        version: version.version,
        fingerprint: version.fingerprint,
      },
      plan: {
        metrics: Array.from(new Set([metric, count, complete].filter(Boolean))),
      },
      value_column: metric,
      count_column: count,
      time_dimension: time,
      completeness_column: complete || null,
      driver_dimensions: drivers
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean),
      related_monitor_ids: [],
      relative_threshold: Number(threshold) / 100,
      absolute_threshold: 0,
      minimum_samples: Number(minimum),
      baseline_weeks: 4,
      window_hours: Number(hours),
      cooldown_hours: Number(hours),
      cadence_minutes: Number(cadence),
      timezone,
      enabled,
    };
    const signature = JSON.stringify(configuration);
    if (operation.current.signature !== signature)
      operation.current = { signature, id: crypto.randomUUID() };
    await api.post("/intelligence/monitors", {
      configuration,
      operation_id: operation.current.id,
    });
    await monitors.refetch();
    setMessage("Monitor saved.");
    setName("");
  }

  async function toggle(monitor: Monitor) {
    const configuration = Object.fromEntries(
      Object.entries(monitor).filter(
        ([key]) =>
          !["id", "revision", "scope", "created_at", "updated_at"].includes(
            key,
          ),
      ),
    );
    await api.put(`/intelligence/monitors/${monitor.id}`, {
      expected_revision: monitor.revision,
      configuration: { ...configuration, enabled: !monitor.enabled },
    });
    await monitors.refetch();
    setMessage(monitor.enabled ? "Schedule disabled." : "Schedule enabled.");
  }

  const choice = (
    label: string,
    suffix: string,
    value: string,
    change: (value: string) => void,
    options: string[],
  ) => (
    <div className="min-w-0 space-y-2">
      <Label htmlFor={`${id}-${suffix}`}>{label}</Label>
      <Select value={value} onValueChange={change}>
        <SelectTrigger id={`${id}-${suffix}`} className="w-full">
          <SelectValue placeholder="Choose a field" />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
  return (
    <section
      className="min-w-0 space-y-4 rounded-md border p-4"
      aria-label="Monitor settings"
    >
      <h2 className="font-medium">Monitors</h2>
      <p className="text-sm text-muted-foreground">
        Compare complete periods against four matching weekday baselines.
        Schedules require an authorized service principal.
      </p>
      <details>
        <summary className="cursor-pointer text-sm font-medium">
          Create a monitor
        </summary>
        <form
          className="mt-4 grid min-w-0 gap-4 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            void run(create);
          }}
        >
          <div className="space-y-2">
            <Label htmlFor={`${id}-name`}>Monitor name</Label>
            <Input
              id={`${id}-name`}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={256}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${id}-agent`}>Agent</Label>
            <Select
              value={agentId}
              onValueChange={(value) => {
                setAgentId(value);
                setViewId("");
                setMetric("");
                setCount("");
                setTime("");
              }}
            >
              <SelectTrigger id={`${id}-agent`} className="w-full">
                <SelectValue placeholder="Choose an agent" />
              </SelectTrigger>
              <SelectContent>
                {(!agents.isError && !agents.isFetching
                  ? (agents.data?.agents ?? [])
                  : []
                ).map((a) => (
                  <SelectItem key={a.agent_id} value={a.agent_id}>
                    {a.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {choice(
            "Published Semantic View",
            "view",
            activeView,
            (value) => {
              setViewId(value);
              setMetric("");
              setCount("");
              setTime("");
            },
            agent?.semantic_view_ids ?? [],
          )}
          {choice("Monitored metric", "metric", metric, setMetric, metricNames)}
          {choice("Sample count metric", "count", count, setCount, metricNames)}
          {choice("Observation time", "time", time, setTime, dimensions)}
          <div className="space-y-2">
            <Label htmlFor={`${id}-drivers`}>
              Driver dimensions, separated by commas
            </Label>
            <Input
              id={`${id}-drivers`}
              value={drivers}
              onChange={(e) => setDrivers(e.target.value)}
              placeholder="orders.city, orders.category"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${id}-complete`}>
              Completeness metric (optional)
            </Label>
            <Input
              id={`${id}-complete`}
              value={complete}
              onChange={(e) => setComplete(e.target.value)}
              placeholder="Value from 0 to 1"
            />
          </div>
          {[
            {
              label: "Minimum change (%)",
              value: threshold,
              set: setThreshold,
              min: 0.01,
              max: 10000,
            },
            {
              label: "Minimum sample count",
              value: minimum,
              set: setMinimum,
              min: 2,
              max: 100000000,
            },
            {
              label: "Observation period (hours)",
              value: hours,
              set: setHours,
              min: 1,
              max: 168,
            },
            {
              label: "Cadence (minutes)",
              value: cadence,
              set: setCadence,
              min: 15,
              max: 1440,
            },
          ].map((field, index) => (
            <div key={field.label} className="space-y-2">
              <Label htmlFor={`${id}-number-${index}`}>{field.label}</Label>
              <Input
                id={`${id}-number-${index}`}
                type="number"
                required
                min={field.min}
                max={field.max}
                step={index === 0 ? 0.01 : 1}
                value={field.value}
                onChange={(e) => field.set(e.target.value)}
              />
            </div>
          ))}
          <div className="space-y-2">
            <Label htmlFor={`${id}-timezone`}>Business timezone</Label>
            <Input
              id={`${id}-timezone`}
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              required
            />
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id={`${id}-enable`}
              checked={enabled}
              onCheckedChange={(v) => setEnabled(v === true)}
            />
            <Label htmlFor={`${id}-enable`}>Enable scheduled monitoring</Label>
          </div>
          <Button
            type="submit"
            disabled={
              busy || !version || !metric || !count || !time || !agentId
            }
          >
            Save monitor
          </Button>
        </form>
      </details>
      {(agents.isError || view.isError || monitors.isError) && (
        <p role="alert" className="text-sm text-destructive">
          Monitor configuration could not be loaded under your current access.
        </p>
      )}
      {monitors.isFetching && !monitors.isFetchingNextPage ? (
        <p role="status" className="text-sm">
          Checking monitors…
        </p>
      ) : (
        !monitors.isError && (
          <ul className="divide-y">
            {monitors.data?.pages
              .flatMap((p) => p.items)
              .map((monitor) => (
                <li key={monitor.id} className="space-y-2 py-3">
                  <h3 className="break-words text-sm font-medium">
                    {monitor.name}
                  </h3>
                  <p className="text-xs text-muted-foreground">
                    {monitor.enabled ? "Scheduled" : "Manual"} ·{" "}
                    {monitor.cadence_minutes} minutes · Semantic version{" "}
                    {monitor.semantic.version}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => void run(() => toggle(monitor))}
                    >
                      {monitor.enabled ? "Disable schedule" : "Enable schedule"}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          const end = new Date();
                          end.setMinutes(0, 0, 0);
                          const start = new Date(
                            end.getTime() - monitor.window_hours * 3600000,
                          );
                          const result = await api.post<{
                            news_id: string | null;
                          }>(`/intelligence/monitors/${monitor.id}/run`, {
                            start: start.toISOString(),
                            end: end.toISOString(),
                          });
                          if (result.news_id) onNews(result.news_id);
                          else
                            setMessage(
                              "The latest complete period did not meet the materiality and sample requirements.",
                            );
                        })
                      }
                    >
                      Check latest period
                    </Button>
                  </div>
                </li>
              ))}
          </ul>
        )
      )}
      {monitors.hasNextPage && (
        <Button
          variant="outline"
          disabled={monitors.isFetchingNextPage}
          onClick={() => void monitors.fetchNextPage()}
        >
          Load more monitors
        </Button>
      )}
      {error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
    </section>
  );
}
