import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
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
import { api } from "@/lib/api-client";
import type { Decision, Investigation, News } from "./lifecycle-api";

type Option = {
  id: string;
  description: string;
  action_type: string;
  change: string;
  uncertainty: string;
  cost: string;
  discount: string;
};
const newOption = (): Option => ({
  id: crypto.randomUUID(),
  description: "",
  action_type: "inventory_transfer",
  change: "",
  uncertainty: "",
  cost: "",
  discount: "0",
});
const fields = [
  { key: "change", label: "Expected change in units" },
  { key: "uncertainty", label: "Unit sensitivity, ±" },
  { key: "cost", label: "Action cost" },
  { key: "discount", label: "Discount fraction (0–1)" },
] as const;

export function DecisionCompose({
  news,
  investigation,
  onCreated,
}: {
  news: News;
  investigation: Investigation;
  onCreated: (id: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [start, setStart] = useState("");
  const [currency, setCurrency] = useState("IDR");
  const [baseline, setBaseline] = useState({
    units: "",
    price: "",
    unitCost: "",
    capacity: "",
    budget: "",
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
      const body = {
        title,
        investigation_id: investigation.id,
        currency,
        outcome_window: {
          start: begins.toISOString(),
          end: new Date(begins.getTime() + duration).toISOString(),
        },
        options: options.map((option) => ({
          id: option.id,
          description: option.description,
          simulation: {
            action_type: option.action_type,
            baseline_units: Number(baseline.units),
            price: Number(baseline.price),
            unit_cost: Number(baseline.unitCost),
            capacity: Number(baseline.capacity),
            max_budget: Number(baseline.budget),
            expected_unit_change: Number(option.change),
            unit_change_uncertainty: Number(option.uncertainty),
            action_cost: Number(option.cost),
            discount: Number(option.discount),
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
  const setOption = (id: string, key: keyof Option, value: string) =>
    setOptions((items) =>
      items.map((item) => (item.id === id ? { ...item, [key]: value } : item)),
    );
  return (
    <details className="rounded-md border p-4">
      <summary className="cursor-pointer font-medium focus-visible:outline focus-visible:outline-ring">
        Prepare a decision
      </summary>
      <form
        className="mt-4 space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <p className="text-sm text-muted-foreground">
          Compare conditional revenue scenarios. Baseline units × unit price
          must match the observed result ({news.after}). The outcome period uses
          the same duration as this incident.
        </p>
        <div className="space-y-2">
          <Label htmlFor="decision-title">Decision title</Label>
          <Input
            id="decision-title"
            required
            maxLength={256}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="outcome-start">Outcome period starts</Label>
            <Input
              id="outcome-start"
              type="datetime-local"
              required
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="decision-currency">Published metric currency</Label>
            <Input
              id="decision-currency"
              required
              minLength={3}
              maxLength={3}
              value={currency}
              onChange={(event) =>
                setCurrency(event.target.value.toUpperCase())
              }
            />
          </div>
        </div>
        <fieldset className="grid gap-3 sm:grid-cols-2">
          <legend className="mb-2 text-sm font-medium">
            Shared unit economics
          </legend>
          {(
            [
              { key: "units", label: "Baseline units" },
              { key: "price", label: "Unit price" },
              { key: "unitCost", label: "Unit cost" },
              { key: "capacity", label: "Capacity in the outcome period" },
              { key: "budget", label: "Maximum action budget" },
            ] as const
          ).map(({ key, label }) => (
            <div key={key} className="space-y-2">
              <Label htmlFor={`economics-${key}`}>{label}</Label>
              <Input
                id={`economics-${key}`}
                type="number"
                min={0}
                step="any"
                required
                value={baseline[key]}
                onChange={(event) =>
                  setBaseline((current) => ({
                    ...current,
                    [key]: event.target.value,
                  }))
                }
              />
            </div>
          ))}
        </fieldset>
        {options.map((option, index) => (
          <fieldset key={option.id} className="space-y-3 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">
              Option {index + 1}
            </legend>
            <div className="space-y-2">
              <Label htmlFor={`description-${option.id}`}>Description</Label>
              <Input
                id={`description-${option.id}`}
                value={option.description}
                maxLength={2000}
                required
                onChange={(event) =>
                  setOption(option.id, "description", event.target.value)
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`action-${option.id}`}>Action to simulate</Label>
              <Select
                value={option.action_type}
                onValueChange={(value) =>
                  setOption(option.id, "action_type", value)
                }
              >
                <SelectTrigger id={`action-${option.id}`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[
                    ["inventory_transfer", "Inventory transfer"],
                    ["campaign_budget", "Campaign budget"],
                    ["rollback", "Service rollback"],
                    ["discount", "Discount"],
                    ["spend", "Spend"],
                  ].map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {fields.map(({ key, label }) => (
                <div key={key} className="space-y-2">
                  <Label htmlFor={`${key}-${option.id}`}>{label}</Label>
                  <Input
                    id={`${key}-${option.id}`}
                    type="number"
                    step="any"
                    min={key === "change" ? undefined : 0}
                    max={key === "discount" ? 0.999 : undefined}
                    required
                    value={option[key]}
                    onChange={(event) =>
                      setOption(option.id, key, event.target.value)
                    }
                  />
                </div>
              ))}
            </div>
            {options.length > 1 && (
              <Button
                type="button"
                variant="ghost"
                onClick={() =>
                  setOptions((current) =>
                    current.filter((item) => item.id !== option.id),
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
          recommendation for review; it does not execute the simulated business
          action.
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
            disabled={options.length >= 5 || create.isPending}
            onClick={() => setOptions((current) => [...current, newOption()])}
          >
            Add option
          </Button>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Computing options…" : "Save decision options"}
          </Button>
        </div>
      </form>
    </details>
  );
}
