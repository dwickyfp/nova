import { useId, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
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
import { api, ApiError } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import {
  intelligenceApi,
  type Decision,
  type Investigation,
  type News,
} from "./lifecycle-api";
import { ScenarioFields } from "./scenario-fields";
import {
  legacyUnitEconomics,
  scenarioDefaults,
  scenarioParameters,
  validateScenarioSchema,
  type ScenarioDefinition,
} from "./scenario-schema";

type Props = {
  news: News;
  investigation: Investigation;
  missionId?: string;
  threadId?: string;
  onCreated: (id: string) => void;
};
type Option = {
  id: string;
  description: string;
  values: Record<string, string | boolean>;
};

export function DecisionCompose(props: Props) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  return (
    <DecisionWorkspace
      key={`${epoch}:${props.investigation.id}`}
      {...props}
      epoch={epoch}
    />
  );
}

function DecisionWorkspace({ epoch, ...props }: Props & { epoch: number }) {
  const [open, setOpen] = useState(false);
  const [definitionId, setDefinitionId] = useState("");
  const definitions = useQuery({
    queryKey: ["intelligence", epoch, "scenarios"],
    queryFn: ({ signal }) => intelligenceApi.scenarios(signal),
    enabled: open,
    retry: false,
    gcTime: 0,
  });
  const legacy =
    definitions.isError &&
    definitions.error instanceof ApiError &&
    [404, 503].includes(definitions.error.status);
  const items = legacy
    ? [legacyUnitEconomics]
    : (definitions.data?.items ?? []);
  const definition = items.find((item) => item.id === definitionId) ?? items[0];
  const id = useId();
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="min-w-0 rounded-md border p-4"
    >
      <summary className="min-h-11 cursor-pointer font-medium focus-visible:outline focus-visible:outline-ring">
        Prepare a decision
      </summary>
      {open && (
        <div className="mt-4 min-w-0 space-y-4">
          {definitions.isPending ? (
            <p role="status" className="text-sm">
              Loading registered scenarios…
            </p>
          ) : definitions.isError && !legacy ? (
            <div role="alert" className="space-y-2">
              <p className="text-sm text-destructive">
                Registered scenarios could not be loaded:{" "}
                {definitions.error.message}
              </p>
              <Button
                variant="outline"
                className="min-h-11"
                onClick={() => void definitions.refetch()}
              >
                Retry scenarios
              </Button>
            </div>
          ) : !definition ? (
            <p className="text-sm text-muted-foreground">
              No scenario models are registered. A registered model is required
              to compare decision options.
            </p>
          ) : (
            <>
              {items.length > 1 && (
                <div className="space-y-2">
                  <Label htmlFor={`${id}-scenario`}>Registered scenario</Label>
                  <Select value={definition.id} onValueChange={setDefinitionId}>
                    <SelectTrigger
                      id={`${id}-scenario`}
                      className="min-h-11 w-full"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {items.map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.title} · version {item.version}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              {legacy && (
                <p className="text-sm text-muted-foreground">
                  This deployment uses the existing unit-economics scenario.
                </p>
              )}
              <ScenarioDecisionForm
                key={`${definition.id}:${definition.version}`}
                {...props}
                definition={definition}
              />
            </>
          )}
        </div>
      )}
    </details>
  );
}

function ScenarioDecisionForm({
  definition,
  ...props
}: Props & { definition: ScenarioDefinition }) {
  let schemas;
  try {
    schemas = {
      shared: validateScenarioSchema(definition.shared_input_schema),
      option: validateScenarioSchema(definition.input_schema),
    };
    const sharedNames = Object.keys(schemas.shared.properties);
    const optionNames = Object.keys(schemas.option.properties);
    if (
      (!sharedNames.length && !optionNames.length) ||
      sharedNames.length + optionNames.length > 32 ||
      sharedNames.some((name) => optionNames.includes(name))
    )
      throw new Error("This scenario has inconsistent fields.");
  } catch (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error instanceof Error
          ? error.message
          : "This scenario cannot be rendered safely."}
      </p>
    );
  }
  return (
    <DecisionForm
      {...props}
      definition={definition}
      sharedSchema={schemas.shared}
      optionSchema={schemas.option}
    />
  );
}

function DecisionForm({
  news,
  investigation,
  onCreated,
  definition,
  sharedSchema,
  optionSchema,
  missionId,
  threadId,
}: Props & {
  definition: ScenarioDefinition;
  sharedSchema: ScenarioDefinition["shared_input_schema"];
  optionSchema: ScenarioDefinition["input_schema"];
}) {
  const id = useId();
  const [title, setTitle] = useState("");
  const [start, setStart] = useState("");
  const [currency, setCurrency] = useState("IDR");
  const [baseline, setBaseline] = useState(() =>
    scenarioDefaults(sharedSchema),
  );
  const newOption = (): Option => ({
    id: crypto.randomUUID(),
    description: "",
    values: scenarioDefaults(optionSchema),
  });
  const [options, setOptions] = useState<Option[]>(() => [newOption()]);
  const pendingOperation = useRef<{ signature: string; id: string } | null>(
    null,
  );
  const create = useMutation({
    mutationFn: () => {
      const begins = new Date(start);
      const duration =
        new Date(news.window.end).getTime() -
        new Date(news.window.start).getTime();
      if (
        !Number.isFinite(begins.getTime()) ||
        !Number.isFinite(duration) ||
        duration <= 0
      )
        throw new Error(
          "Choose a valid outcome period. The observed comparison must have a positive duration.",
        );
      const shared = scenarioParameters(sharedSchema, baseline);
      const body = {
        title: title.trim(),
        investigation_id: investigation.id,
        ...(missionId
          ? {
              mission_id: missionId,
              investigation_revision: investigation.revision,
            }
          : {}),
        ...(threadId ? { thread_id: threadId } : {}),
        ...(definition.currency_required !== false ? { currency } : {}),
        outcome_window: {
          start: begins.toISOString(),
          end: new Date(begins.getTime() + duration).toISOString(),
        },
        options: options.map((option) => ({
          id: option.id,
          description: option.description.trim(),
          scenario_kind: definition.scenario_kind,
          scenario_version: definition.version,
          parameters: {
            ...shared,
            ...scenarioParameters(optionSchema, option.values),
          },
        })),
      };
      const signature = JSON.stringify(body);
      if (pendingOperation.current?.signature !== signature)
        pendingOperation.current = { signature, id: crypto.randomUUID() };
      return api.post<Decision>("/intelligence/decisions", {
        ...body,
        operation_id: pendingOperation.current.id,
      });
    },
    onSuccess: (decision) => onCreated(decision.id),
  });
  return (
    <form
      className="min-w-0 space-y-5"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <p className="text-sm text-muted-foreground">
        {definition.description} The server validates these assumptions against
        the observed result ({news.after}) and published target metric. The
        outcome period uses the same duration as this incident.
      </p>
      <div className="space-y-2">
        <Label htmlFor={`${id}-title`}>Decision title</Label>
        <Input
          id={`${id}-title`}
          className="min-h-11"
          required
          maxLength={256}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          disabled={create.isPending}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor={`${id}-start`}>Outcome period starts</Label>
          <Input
            id={`${id}-start`}
            className="min-h-11"
            type="datetime-local"
            required
            value={start}
            onChange={(event) => setStart(event.target.value)}
            disabled={create.isPending}
          />
        </div>
        {definition.currency_required !== false && (
          <div className="space-y-2">
            <Label htmlFor={`${id}-currency`}>Published metric currency</Label>
            <Input
              id={`${id}-currency`}
              className="min-h-11"
              required
              minLength={3}
              maxLength={3}
              pattern="[A-Z]{3}"
              value={currency}
              onChange={(event) =>
                setCurrency(event.target.value.toUpperCase())
              }
              disabled={create.isPending}
            />
          </div>
        )}
      </div>
      {Object.keys(sharedSchema.properties).length > 0 && (
        <fieldset className="min-w-0 space-y-3">
          <legend className="mb-3 text-sm font-medium">
            Shared {definition.title.toLowerCase()} assumptions
          </legend>
          <ScenarioFields
            schema={sharedSchema}
            values={baseline}
            onChange={(key, value) =>
              setBaseline((current) => ({ ...current, [key]: value }))
            }
            disabled={create.isPending}
          />
        </fieldset>
      )}
      {options.map((option, index) => (
        <fieldset
          key={option.id}
          className="min-w-0 space-y-4 rounded-md border p-3"
        >
          <legend className="px-1 text-sm font-medium">
            Option {index + 1}
          </legend>
          <div className="space-y-2">
            <Label htmlFor={`${id}-description-${option.id}`}>
              Description
            </Label>
            <Input
              id={`${id}-description-${option.id}`}
              className="min-h-11"
              value={option.description}
              maxLength={2000}
              required
              disabled={create.isPending}
              onChange={(event) =>
                setOptions((items) =>
                  items.map((item) =>
                    item.id === option.id
                      ? { ...item, description: event.target.value }
                      : item,
                  ),
                )
              }
            />
          </div>
          <ScenarioFields
            schema={optionSchema}
            values={option.values}
            disabled={create.isPending}
            onChange={(key, value) =>
              setOptions((items) =>
                items.map((item) =>
                  item.id === option.id
                    ? { ...item, values: { ...item.values, [key]: value } }
                    : item,
                ),
              )
            }
          />
          {options.length > 1 && (
            <Button
              type="button"
              variant="ghost"
              className="min-h-11"
              disabled={create.isPending}
              onClick={() =>
                setOptions((items) =>
                  items.filter((item) => item.id !== option.id),
                )
              }
            >
              Remove option {index + 1}
            </Button>
          )}
        </fieldset>
      ))}
      <p className="text-xs text-muted-foreground">
        Estimates depend on these assumptions. Saving options records a
        recommendation for review; execution requires its own approval and
        consent.
      </p>
      {create.isError && (
        <p role="alert" className="text-sm text-destructive">
          {create.error.message}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          className="min-h-11"
          disabled={options.length >= 5 || create.isPending}
          onClick={() => setOptions((items) => [...items, newOption()])}
        >
          Add option
        </Button>
        <Button type="submit" className="min-h-11" disabled={create.isPending}>
          {create.isPending ? "Computing options…" : "Save decision options"}
        </Button>
      </div>
    </form>
  );
}
