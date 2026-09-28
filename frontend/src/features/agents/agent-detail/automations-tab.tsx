import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  automationsApi,
  type Automation,
  type AutomationCondition,
} from "@/features/agents/studio-intelligence-api";

const PRESETS = {
  daily: { label: "Every day at 08:00", expr: "0 8 * * *" },
  weekly: { label: "Every Monday at 08:00", expr: "0 8 * * 1" },
  monthly: { label: "The 1st of each month at 08:00", expr: "0 8 1 * *" },
  custom: { label: "Custom cron", expr: "" },
} as const;
type Preset = keyof typeof PRESETS;

/**
 * Automations: questions this agent answers on a schedule, read-only, as you.
 * Each run lands in Studio history; a condition turns a report into an alert.
 */
export function AgentAutomationsTab({ agentId }: { agentId: string }) {
  const query = useQuery({
    queryKey: ["agents", "automations", agentId],
    queryFn: () => automationsApi.list(agentId),
  });
  const automations = query.data?.automations ?? [];
  return (
    <div className="grid max-w-4xl gap-8">
      <section aria-labelledby="automations-heading" className="space-y-3">
        <div>
          <h2 id="automations-heading" className="text-base font-medium">Scheduled questions</h2>
          <p className="text-sm text-muted-foreground">
            Runs are read-only and use your access. Results appear in Nova Studio history.
          </p>
        </div>
        {query.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : query.isError ? (
          <p role="alert" className="text-sm text-destructive">Automations could not be loaded.</p>
        ) : automations.length === 0 ? (
          <p className="text-sm text-muted-foreground">No automations yet.</p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {automations.map((item) => (
              <AutomationRow key={item.automation_id} agentId={agentId} automation={item} />
            ))}
          </ul>
        )}
      </section>
      <NewAutomation agentId={agentId} />
    </div>
  );
}

function describeStatus(status: string | null): string {
  if (!status) return "Not run yet";
  if (status.startsWith("delivered")) return "Delivered";
  if (status === "condition_not_met") return "Condition not met, nothing sent";
  if (status === "condition_unavailable") return "The condition metric was not in the result";
  return "Failed";
}

function AutomationRow({ agentId, automation }: { agentId: string; automation: Automation }) {
  const queryClient = useQueryClient();
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["agents", "automations", agentId] });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      automationsApi.update(agentId, automation.automation_id, { enabled }),
    onSuccess: refresh,
    onError: (error: Error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: () => automationsApi.remove(agentId, automation.automation_id),
    onSuccess: () => {
      toast.success("Automation deleted");
      refresh();
    },
    onError: (error: Error) => toast.error(error.message),
  });
  return (
    <li className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium break-words">{automation.title}</p>
        <p className="text-sm text-muted-foreground break-words">{automation.prompt}</p>
        <p className="text-xs text-muted-foreground">
          <code>{automation.schedule_expr}</code> · {automation.timezone}
          {automation.condition
            ? ` · only when ${automation.condition.metric} ${automation.condition.operator} ${automation.condition.value}`
            : ""}
        </p>
        <p className="text-xs text-muted-foreground">
          {describeStatus(automation.last_status)}
          {automation.next_run_at && automation.enabled
            ? ` · next run ${new Date(`${automation.next_run_at}Z`).toLocaleString()}`
            : ""}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Switch
          checked={automation.enabled}
          disabled={toggle.isPending}
          onCheckedChange={(enabled) => toggle.mutate(enabled)}
          aria-label={automation.enabled ? "Pause automation" : "Resume automation"}
        />
        <Button
          variant="ghost"
          size="icon"
          disabled={remove.isPending}
          onClick={() => remove.mutate()}
          aria-label="Delete automation"
        >
          <Trash2 className="size-4" />
        </Button>
      </div>
    </li>
  );
}

function NewAutomation({ agentId }: { agentId: string }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [preset, setPreset] = useState<Preset>("weekly");
  const [cron, setCron] = useState("");
  const [metric, setMetric] = useState("");
  const [operator, setOperator] = useState<AutomationCondition["operator"]>("<");
  const [threshold, setThreshold] = useState("");
  const expression = preset === "custom" ? cron.trim() : PRESETS[preset].expr;
  const create = useMutation({
    mutationFn: () =>
      automationsApi.create(agentId, {
        title: title.trim(),
        prompt: prompt.trim(),
        schedule_kind: "cron",
        schedule_expr: expression,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        condition:
          metric.trim() && threshold.trim()
            ? { metric: metric.trim(), operator, value: Number(threshold) }
            : null,
      }),
    onSuccess: () => {
      toast.success("Automation scheduled");
      setTitle("");
      setPrompt("");
      setMetric("");
      setThreshold("");
      queryClient.invalidateQueries({ queryKey: ["agents", "automations", agentId] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const valid = title.trim() && prompt.trim().length >= 3 && expression &&
    (!threshold.trim() || Number.isFinite(Number(threshold)));
  return (
    <section aria-labelledby="new-automation-heading" className="space-y-4">
      <h2 id="new-automation-heading" className="text-base font-medium">New automation</h2>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="au-title">Name</Label>
          <Input id="au-title" value={title} onChange={(e) => setTitle(e.target.value)}
            placeholder="Weekly revenue" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="au-when">When</Label>
          <Select value={preset} onValueChange={(value) => setPreset(value as Preset)}>
            <SelectTrigger id="au-when"><SelectValue /></SelectTrigger>
            <SelectContent>
              {(Object.keys(PRESETS) as Preset[]).map((key) => (
                <SelectItem key={key} value={key}>{PRESETS[key].label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {preset === "custom" && (
            <Input aria-label="Cron expression" value={cron} placeholder="0 8 * * 1-5"
              onChange={(e) => setCron(e.target.value)} />
          )}
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="au-prompt">Question</Label>
        <Textarea id="au-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)}
          placeholder="Berapa penjualan minggu lalu per kota, dibanding minggu sebelumnya?"
          className="min-h-20" />
      </div>
      <fieldset className="space-y-1.5">
        <legend className="text-sm font-medium">Only send when (optional)</legend>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input aria-label="Metric" value={metric} placeholder="total_revenue"
            onChange={(e) => setMetric(e.target.value)} />
          <Select value={operator}
            onValueChange={(value) => setOperator(value as AutomationCondition["operator"])}>
            <SelectTrigger aria-label="Comparison" className="sm:w-28"><SelectValue /></SelectTrigger>
            <SelectContent>
              {(["<", "<=", ">", ">=", "=", "!="] as const).map((item) => (
                <SelectItem key={item} value={item}>{item}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input aria-label="Threshold" inputMode="decimal" value={threshold}
            placeholder="10000000" onChange={(e) => setThreshold(e.target.value)} />
        </div>
      </fieldset>
      <Button disabled={!valid || create.isPending} onClick={() => create.mutate()}>
        Schedule
      </Button>
    </section>
  );
}
