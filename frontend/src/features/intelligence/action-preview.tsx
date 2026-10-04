import { useId, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { useAuthStore } from "@/stores/auth-store";
import {
  actionApi,
  intelligenceApi,
  type BusinessAction,
  type ActionAdapterInput,
  type AutomationActionConfiguration,
  type Decision,
  type MonitorConfiguration,
} from "./lifecycle-api";
import { ActionLifecycle } from "./action-lifecycle";

type PreviewProps = {
  decision: Decision;
  missionId?: string;
  threadId: string;
  canReview?: boolean;
  onCreated?: (action: BusinessAction) => void;
};

export function MonitorActionPreview(
  props: PreviewProps & { configuration: MonitorConfiguration },
) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  return <ChooseAction key={`${epoch}:${props.decision.id}`} {...props} />;
}

function ChooseAction(
  props: PreviewProps & { configuration: MonitorConfiguration },
) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const id = useId();
  const [adapter, setAdapter] = useState("monitor-v1");
  const [title, setTitle] = useState(
    `${props.decision.title || "Business decision"} report`,
  );
  const [prompt, setPrompt] = useState(
    `Report ${props.configuration.value_column} from the governed Semantic View and describe changes with supporting evidence.`,
  );
  const [schedule, setSchedule] = useState("0 8 * * 1");
  const [timezone, setTimezone] = useState(props.configuration.timezone);
  const configuredTimezone = useQuery({
    queryKey: ["studio", epoch, "execution-timezone"],
    queryFn: ({ signal }) => intelligenceApi.executionTimezone(signal),
    enabled: props.configuration.timezone === undefined,
    retry: false,
    gcTime: 0,
  });
  const monitorTimezone =
    props.configuration.timezone ?? configuredTimezone.data ?? "";
  const reportTimezone = timezone ?? monitorTimezone;
  const [created, setCreated] = useState(false);
  const onCreated = (action: BusinessAction) => {
    setCreated(true);
    props.onCreated?.(action);
  };
  const valid =
    title.trim().length > 0 &&
    prompt.trim().length >= 3 &&
    schedule.trim().length > 0 &&
    reportTimezone.trim().length > 0;
  return (
    <div className="min-w-0 space-y-4">
      {!created && (
        <>
          <RadioGroup
            aria-label="Action type"
            value={adapter}
            onValueChange={setAdapter}
            className="flex flex-wrap gap-x-5 gap-y-2"
          >
            <Label
              htmlFor={`${id}-monitor`}
              className="flex min-h-11 cursor-pointer items-center gap-2"
            >
              <RadioGroupItem id={`${id}-monitor`} value="monitor-v1" /> Metric
              monitor
            </Label>
            <Label
              htmlFor={`${id}-automation`}
              className="flex min-h-11 cursor-pointer items-center gap-2"
            >
              <RadioGroupItem id={`${id}-automation`} value="automation-v1" />{" "}
              Scheduled Studio report
            </Label>
          </RadioGroup>
          {adapter === "automation-v1" && (
            <div className="min-w-0 space-y-3">
              <div className="space-y-2">
                <Label htmlFor={`${id}-title`}>Report title</Label>
                <Input
                  id={`${id}-title`}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  maxLength={256}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor={`${id}-prompt`}>Question for each report</Label>
                <Textarea
                  id={`${id}-prompt`}
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  maxLength={4000}
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor={`${id}-schedule`}>Cron schedule</Label>
                  <Input
                    id={`${id}-schedule`}
                    value={schedule}
                    onChange={(event) => setSchedule(event.target.value)}
                    maxLength={128}
                    aria-describedby={`${id}-schedule-help`}
                  />
                  <p
                    id={`${id}-schedule-help`}
                    className="text-sm text-muted-foreground"
                  >
                    0 8 * * 1 runs every Monday at 08:00.
                  </p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`${id}-timezone`}>Timezone</Label>
                  <Input
                    id={`${id}-timezone`}
                    value={reportTimezone}
                    onChange={(event) => setTimezone(event.target.value)}
                    maxLength={64}
                  />
                </div>
              </div>
            </div>
          )}
          {props.configuration.timezone === undefined &&
            (adapter === "monitor-v1" || timezone === undefined) &&
            !monitorTimezone &&
            (configuredTimezone.isError ? (
              <div role="alert" className="space-y-2 text-sm">
                <p className="text-destructive">
                  {configuredTimezone.error.message}
                </p>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void configuredTimezone.refetch()}
                >
                  Retry timezone
                </Button>
              </div>
            ) : (
              <p role="status" className="text-sm">
                Loading configured timezone…
              </p>
            ))}
        </>
      )}
      {adapter === "automation-v1" ? (
        <Preview
          key="automation-v1"
          {...props}
          adapter_id="automation-v1"
          onCreated={onCreated}
          ready={valid}
          configuration={{
            agent_id: props.configuration.agent_id,
            semantic: props.configuration.semantic,
            title,
            prompt,
            schedule_kind: "cron",
            schedule_expr: schedule,
            timezone: reportTimezone,
            enabled: true,
            delivery: "studio",
          }}
        />
      ) : (
        <Preview
          key="monitor-v1"
          {...props}
          adapter_id="monitor-v1"
          ready={Boolean(monitorTimezone.trim())}
          configuration={{
            ...props.configuration,
            timezone: monitorTimezone,
          }}
          onCreated={onCreated}
        />
      )}
    </div>
  );
}

export function AutomationActionPreview(
  props: PreviewProps & { configuration: AutomationActionConfiguration },
) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  return (
    <Preview
      key={`${epoch}:${props.decision.id}`}
      adapter_id="automation-v1"
      {...props}
    />
  );
}

function Preview(
  props: PreviewProps & ActionAdapterInput & { ready?: boolean },
) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const { decision, threadId, canReview, onCreated } = props;
  const missionId = props.missionId ?? decision.mission_id ?? undefined;
  const automation = props.adapter_id === "automation-v1";
  const [actionId, setActionId] = useState("");
  const key = useRef<{ signature: string; id: string } | null>(null);
  const preview = useMutation({
    mutationFn: () => {
      if (!decision.selected_option_id)
        throw new Error(
          "Select a decision option before previewing an action.",
        );
      const adapter: ActionAdapterInput =
        props.adapter_id === "automation-v1"
          ? { adapter_id: "automation-v1", configuration: props.configuration }
          : { adapter_id: "monitor-v1", configuration: props.configuration };
      const body = {
        decision_id: decision.id,
        expected_decision_revision: decision.revision,
        option_id: decision.selected_option_id,
        ...adapter,
        ...(missionId ? { mission_id: missionId } : {}),
      };
      const signature = JSON.stringify(body);
      if (key.current?.signature !== signature)
        key.current = { signature, id: crypto.randomUUID() };
      return actionApi.preview({ ...body, idempotency_key: key.current.id });
    },
    onSuccess: (action) => {
      if (useAuthStore.getState().securityEpoch !== epoch) return;
      setActionId(action.id);
      onCreated?.(action);
    },
  });
  if (actionId)
    return (
      <ActionLifecycle
        actionId={actionId}
        threadId={threadId}
        canReview={canReview}
      />
    );
  return (
    <section className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {automation
          ? "Schedule an agent report for this decision in Studio. Preview evaluates policy before creating the automation."
          : "Create a governed monitor for this decision. Preview evaluates policy before any monitor or schedule is created."}
      </p>
      <Button
        className="min-h-11"
        disabled={
          preview.isPending ||
          !decision.selected_option_id ||
          props.ready === false
        }
        onClick={() => preview.mutate()}
      >
        {preview.isPending
          ? "Preparing action preview…"
          : automation
            ? "Preview automation action"
            : "Preview monitor action"}
      </Button>
      {preview.isError && (
        <p role="alert" className="text-sm text-destructive">
          {preview.error.message}
        </p>
      )}
    </section>
  );
}
