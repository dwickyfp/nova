import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/ui/status-badge";
import { Switch } from "@/components/ui/switch";
import {
  semanticViewsApi,
  type NewsConfig,
  type NewsDefaults,
  type SemanticView,
} from "./semantic-views-api";

const CADENCE = [
  { minutes: 15, label: "Every 15 minutes" },
  { minutes: 60, label: "Every hour" },
  { minutes: 360, label: "Every 6 hours" },
  { minutes: 1440, label: "Once a day" },
];
const select = "flex h-10 w-full rounded-md border bg-background px-3 text-sm";
const humanize = (name: string) => name.split("_").join(" ");

function Choices({
  legend,
  options,
  chosen,
  limit,
  onChange,
}: {
  legend: string;
  options: string[];
  chosen: string[];
  limit: number;
  onChange: (next: string[]) => void;
}) {
  return (
    <fieldset className="min-w-0 space-y-2">
      <legend className="text-sm">
        {legend} <span className="text-muted-foreground">(up to {limit})</span>
      </legend>
      <div className="flex flex-wrap gap-x-4 gap-y-2">
        {options.map((option) => {
          const checked = chosen.includes(option);
          return (
            <label key={option} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={checked}
                disabled={!checked && chosen.length >= limit}
                onChange={() =>
                  onChange(
                    checked
                      ? chosen.filter((item) => item !== option)
                      : [...chosen, option],
                  )
                }
              />
              {humanize(option)}
            </label>
          );
        })}
        {!options.length && (
          <p className="text-sm text-muted-foreground">
            The published version defines none.
          </p>
        )}
      </div>
    </fieldset>
  );
}

function Coverage({
  defaults,
  saving,
  onSave,
  onCancel,
}: {
  defaults: NewsDefaults;
  saving: boolean;
  onSave: (config: NewsConfig) => void;
  onCancel: () => void;
}) {
  const [config, setConfig] = useState<NewsConfig>({
    execution_role: defaults.execution_role,
    metrics: defaults.metrics,
    count_metric: defaults.count_metric,
    slice_dimensions: defaults.slice_dimensions,
    time_dimension: defaults.time_dimension,
    relative_threshold: defaults.relative_threshold ?? 0.1,
    cadence_minutes: defaults.cadence_minutes ?? 60,
    narrative: defaults.narrative ?? "model",
  });
  const set = (patch: Partial<NewsConfig>) =>
    setConfig((current) => ({ ...current, ...patch }));
  const ready =
    config.execution_role.trim() &&
    config.metrics.length &&
    config.count_metric &&
    config.time_dimension;
  return (
    <form
      className="mt-4 grid gap-4 md:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({ ...config, execution_role: config.execution_role.trim() });
      }}
    >
      <div className="md:col-span-2">
        <Choices
          legend="Metrics to watch"
          options={defaults.available.metrics.filter(
            (name) => name !== config.count_metric,
          )}
          chosen={config.metrics}
          limit={3}
          onChange={(metrics) => set({ metrics })}
        />
      </div>
      <div className="md:col-span-2">
        <Choices
          legend="Break down by"
          options={defaults.available.dimensions}
          chosen={config.slice_dimensions}
          limit={3}
          onChange={(slice_dimensions) => set({ slice_dimensions })}
        />
      </div>
      <label className="space-y-1 text-sm">
        Record count metric
        <select
          className={select}
          value={config.count_metric}
          onChange={(event) =>
            set({
              count_metric: event.target.value,
              metrics: config.metrics.filter((name) => name !== event.target.value),
            })
          }
        >
          <option value="">Choose a metric</option>
          {defaults.available.metrics.map((name) => (
            <option key={name} value={name}>
              {humanize(name)}
            </option>
          ))}
        </select>
      </label>
      <label className="space-y-1 text-sm">
        Date
        <select
          className={select}
          value={config.time_dimension}
          onChange={(event) => set({ time_dimension: event.target.value })}
        >
          <option value="">Choose a date</option>
          {defaults.available.time_dimensions.map((name) => (
            <option key={name} value={name}>
              {humanize(name)}
            </option>
          ))}
        </select>
      </label>
      <label className="space-y-1 text-sm">
        Report a change larger than (%)
        <Input
          type="number"
          min={1}
          max={100}
          value={Math.round((config.relative_threshold ?? 0.1) * 100)}
          onChange={(event) =>
            set({ relative_threshold: Number(event.target.value) / 100 })
          }
        />
      </label>
      <label className="space-y-1 text-sm">
        Press a new edition
        <select
          className={select}
          value={config.cadence_minutes}
          onChange={(event) => set({ cadence_minutes: Number(event.target.value) })}
        >
          {CADENCE.map((item) => (
            <option key={item.minutes} value={item.minutes}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <label className="space-y-1 text-sm md:col-span-2">
        Role that reads the data for News
        <Input
          value={config.execution_role}
          onChange={(event) => set({ execution_role: event.target.value })}
        />
        <span className="block text-xs text-muted-foreground">
          Its scheduled account produces the edition. Each reader still sees only
          the stories their own role can reproduce.
        </span>
      </label>
      <label className="flex items-start gap-3 text-sm md:col-span-2">
        <Switch
          checked={config.narrative === "model"}
          onCheckedChange={(on) => set({ narrative: on ? "model" : "template" })}
          aria-label="Let the default model write the stories"
        />
        <span>
          Let the default model write the stories
          <span className="block text-xs text-muted-foreground">
            Sends each story&apos;s figures to the configured model provider. Off
            keeps standard wording and sends nothing.
          </span>
        </span>
      </label>
      <div className="flex flex-wrap gap-2 md:col-span-2">
        <Button type="submit" disabled={saving || !ready}>
          {saving ? "Saving…" : "Save and switch on"}
        </Button>
        <Button type="button" variant="outline" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** The News switch of one Semantic View, with what the edition covers. */
export function NewsSettings({
  view,
  onChanged,
}: {
  view: SemanticView;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const canManage = "news_config" in view;
  const enabled = view.news_enabled === true;
  const defaults = useQuery({
    queryKey: ["semantic-view-news-defaults", view.id],
    queryFn: () => semanticViewsApi.newsDefaults(view.id),
    enabled: editing,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });
  const save = useMutation({
    mutationFn: (body: { enabled: boolean; config?: NewsConfig }) =>
      semanticViewsApi.setNews(view.id, body),
    onSuccess: () => {
      setEditing(false);
      onChanged();
    },
  });
  const stored = view.news_config;
  return (
    <section
      aria-labelledby="semantic-view-news"
      className="min-w-0 rounded-lg border bg-background p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 id="semantic-view-news" className="font-medium">
            News
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Report material changes in this view&apos;s metrics as stories in
            Studio. Readers see only what their role can read.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge tone={enabled ? "success" : "neutral"}>
            {enabled ? "On" : "Off"}
          </StatusBadge>
          {canManage && (
            <Switch
              checked={enabled}
              disabled={save.isPending || !view.active_version}
              aria-label="Publish News from this view"
              onCheckedChange={(on) => {
                if (on) setEditing(true);
                else save.mutate({ enabled: false });
              }}
            />
          )}
        </div>
      </div>
      {canManage && !view.active_version && (
        <p className="mt-3 text-sm text-muted-foreground">
          Publish a version before switching News on.
        </p>
      )}
      {save.isError && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {save.error instanceof Error
            ? save.error.message
            : "News settings could not be saved."}
        </p>
      )}
      {canManage && enabled && stored && !editing && (
        <div className="mt-4 space-y-3 text-sm">
          <dl className="grid gap-3 sm:grid-cols-3">
            <div>
              <dt className="text-muted-foreground">Metrics</dt>
              <dd className="mt-1 break-words">
                {stored.metrics.map(humanize).join(", ")}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Broken down by</dt>
              <dd className="mt-1 break-words">
                {stored.slice_dimensions.map(humanize).join(", ") || "Totals only"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Produced by role</dt>
              <dd className="mt-1 break-all">{stored.execution_role}</dd>
            </div>
          </dl>
          <p className="text-muted-foreground">
            A new edition is pressed on schedule; the first one follows within{" "}
            {stored.cadence_minutes ?? 60} minutes of switching on.
          </p>
          <Button variant="outline" onClick={() => setEditing(true)}>
            Change coverage
          </Button>
        </div>
      )}
      {editing &&
        (defaults.isError ? (
          <p role="alert" className="mt-3 text-sm text-destructive">
            The view&apos;s metrics could not be loaded.{" "}
            <Button variant="link" className="h-auto p-0" onClick={() => void defaults.refetch()}>
              Retry
            </Button>
          </p>
        ) : defaults.data ? (
          <Coverage
            defaults={{ ...defaults.data, ...(stored ?? {}), available: defaults.data.available }}
            saving={save.isPending}
            onSave={(config) => save.mutate({ enabled: true, config })}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <p role="status" className="mt-3 text-sm text-muted-foreground">
            Loading the view&apos;s metrics…
          </p>
        ))}
    </section>
  );
}
