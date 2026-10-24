import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { studioApi, type VizSourceBody } from "@/features/agents/api";
import type { Aggregation, DataPaneField, Filter, RelativeDateRange } from "./types";
import { AGGREGATION_LABELS, NUMERIC_AGGREGATIONS, isTemporal } from "./worksheet-model";

/**
 * FilterDialog edits one filter: a member list for dimensions, a range for
 * numbers and dates, a relative window for dates, or a range on the
 * aggregated value for measures. "Add to context" makes the filter apply
 * before FIXED level-of-detail calculations.
 */

type Mode = "members" | "range" | "relative";

const RELATIVE_OPTIONS: { value: RelativeDateRange; label: string; counted: boolean }[] = [
  { value: "last_n_days", label: "Last N days", counted: true },
  { value: "last_n_months", label: "Last N months", counted: true },
  { value: "last_n_years", label: "Last N years", counted: true },
  { value: "month_to_date", label: "Month to date", counted: false },
  { value: "year_to_date", label: "Year to date", counted: false },
];

function memberText(value: string | number | null): string {
  return value === null ? "Null" : String(value);
}

export function FilterDialog({
  open,
  onOpenChange,
  source,
  field,
  initial,
  asMeasure,
  onSave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source: VizSourceBody;
  field: DataPaneField | null;
  initial: Filter | null;
  /** Filter the aggregated value per mark (a measure filter). */
  asMeasure: boolean;
  onSave: (filter: Filter) => void;
}) {
  const aggregateCalc = field?.level === "aggregate";
  const measureFilter = asMeasure || aggregateCalc;
  const temporal = field ? isTemporal(field) : false;
  const numeric = field?.data_type === "number";
  const defaultMode: Mode =
    initial?.kind === "quantitative"
      ? "range"
      : initial?.kind === "relative_date"
        ? "relative"
        : measureFilter || (numeric && !initial)
          ? "range"
          : "members";

  const [mode, setMode] = useState<Mode>(defaultMode);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [nullIncluded, setNullIncluded] = useState(false);
  const [exclude, setExclude] = useState(false);
  const [search, setSearch] = useState("");
  const [min, setMin] = useState("");
  const [max, setMax] = useState("");
  const [range, setRange] = useState<RelativeDateRange>("last_n_months");
  const [count, setCount] = useState("3");
  const [context, setContext] = useState(false);
  const [aggregation, setAggregation] = useState<Aggregation>("sum");

  useEffect(() => {
    if (!open) return;
    setMode(defaultMode);
    setSearch("");
    setContext(Boolean(initial?.context));
    setSelected(new Set(initial?.kind === "categorical" ? initial.values : []));
    setNullIncluded(initial?.kind === "categorical" ? Boolean(initial.null_included) : false);
    setExclude(initial?.kind === "categorical" ? Boolean(initial.exclude) : false);
    setMin(initial?.kind === "quantitative" && initial.min !== null && initial.min !== undefined ? String(initial.min) : "");
    setMax(initial?.kind === "quantitative" && initial.max !== null && initial.max !== undefined ? String(initial.max) : "");
    setAggregation(
      initial?.kind === "quantitative" && initial.aggregation ? initial.aggregation : "sum",
    );
    setRange(initial?.kind === "relative_date" ? initial.range : "last_n_months");
    setCount(initial?.kind === "relative_date" ? String(initial.n) : "3");
    // The dialog resets whenever it opens for another field or filter.
  }, [open, initial, field?.name]);

  const domain = useQuery({
    queryKey: ["studio", "viz", "domain", source, field?.name],
    queryFn: () => studioApi.vizFieldDomain({ ...source, field: field?.name ?? "" }),
    enabled: open && Boolean(field) && !aggregateCalc && !(measureFilter && !numeric),
    staleTime: 30_000,
  });

  const members = domain.data?.values ?? [];
  const visibleMembers = useMemo(() => {
    const query = search.trim().toLowerCase();
    return query ? members.filter((value) => memberText(value).toLowerCase().includes(query)) : members;
  }, [members, search]);

  if (!field) return null;

  const rangeInvalid =
    mode === "range" &&
    ((min.trim() === "" && max.trim() === "") ||
      (!temporal && [min, max].some((value) => value.trim() !== "" && !Number.isFinite(Number(value)))));
  const countInvalid =
    mode === "relative" &&
    RELATIVE_OPTIONS.find((option) => option.value === range)?.counted &&
    !(Number.isInteger(Number(count)) && Number(count) >= 1 && Number(count) <= 1000);

  const build = (): Filter => {
    if (mode === "range") {
      const bound = (value: string) =>
        value.trim() === "" ? null : temporal ? value.trim() : Number(value);
      return {
        kind: "quantitative",
        field: field.name,
        min: bound(min),
        max: bound(max),
        aggregation: measureFilter && !aggregateCalc ? aggregation : null,
        context: measureFilter ? false : context,
      };
    }
    if (mode === "relative") {
      return { kind: "relative_date", field: field.name, range, n: Number(count) || 1, context };
    }
    const values = [...selected];
    return {
      kind: "categorical",
      field: field.name,
      values,
      exclude,
      null_included: nullIncluded || null,
      context,
    };
  };

  const toggle = (value: string | number | null) => {
    if (value === null) {
      setNullIncluded((current) => !current);
      return;
    }
    setSelected((current) => {
      const next = new Set(current);
      const key = String(value);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] w-full max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Filter {field.name}</DialogTitle>
          <DialogDescription>
            {measureFilter
              ? "Keep marks whose aggregated value falls in the range."
              : "Choose which rows the view keeps."}
          </DialogDescription>
        </DialogHeader>

        {measureFilter && !aggregateCalc ? (
          <div className="flex items-center gap-2">
            <Label htmlFor="filter-aggregation" className="text-xs">
              Aggregation
            </Label>
            <Select value={aggregation} onValueChange={(value) => setAggregation(value as Aggregation)}>
              <SelectTrigger id="filter-aggregation" className="h-8 w-48 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(AGGREGATION_LABELS) as Exclude<Aggregation, "agg">[])
                  .filter((item) => numeric || !NUMERIC_AGGREGATIONS.has(item))
                  .filter((item) => item !== "attr")
                  .map((item) => (
                    <SelectItem key={item} value={item}>
                      {AGGREGATION_LABELS[item]}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        ) : null}

        <Tabs value={mode} onValueChange={(value) => setMode(value as Mode)}>
          <TabsList aria-label="Filter type">
            {!measureFilter ? <TabsTrigger value="members">Values</TabsTrigger> : null}
            {numeric || temporal || measureFilter ? <TabsTrigger value="range">Range</TabsTrigger> : null}
            {temporal && !measureFilter ? <TabsTrigger value="relative">Relative date</TabsTrigger> : null}
          </TabsList>

          <TabsContent value="members" className="mt-3 flex flex-col gap-2">
            <div className="relative">
              <Search
                aria-hidden="true"
                className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search values"
                aria-label="Search values"
                className="h-8 pl-7 text-xs"
              />
            </div>
            <div className="flex items-center gap-2 text-xs">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={() => {
                  setSelected(new Set(members.filter((v) => v !== null).map(String)));
                  setNullIncluded(members.includes(null));
                }}
              >
                Select all
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={() => {
                  setSelected(new Set());
                  setNullIncluded(false);
                }}
              >
                Clear
              </Button>
              <span className="ml-auto text-muted-foreground">
                {selected.size + (nullIncluded ? 1 : 0)} selected
              </span>
            </div>
            <div className="max-h-64 overflow-y-auto rounded-md border p-1" aria-busy={domain.isLoading}>
              {domain.isLoading ? (
                <p className="flex items-center gap-2 p-2 text-xs text-muted-foreground">
                  <Loader2 aria-hidden="true" className="size-3 animate-spin" /> Loading values…
                </p>
              ) : domain.isError ? (
                <p className="p-2 text-xs text-destructive">{(domain.error as Error).message}</p>
              ) : !visibleMembers.length ? (
                <p className="p-2 text-xs text-muted-foreground">No values match.</p>
              ) : (
                <ul>
                  {visibleMembers.map((value) => {
                    const checked = value === null ? nullIncluded : selected.has(String(value));
                    const id = `member-${memberText(value)}`;
                    return (
                      <li key={id} className="flex items-center gap-2 rounded px-2 py-1 hover:bg-muted">
                        <Checkbox id={id} checked={checked} onCheckedChange={() => toggle(value)} />
                        <Label htmlFor={id} className="min-w-0 flex-1 truncate text-xs font-normal">
                          {memberText(value)}
                        </Label>
                      </li>
                    );
                  })}
                </ul>
              )}
              {domain.data?.truncated ? (
                <p className="p-2 text-xs text-muted-foreground">
                  Showing the first {members.length} values; search to narrow them.
                </p>
              ) : null}
            </div>
            <label className="flex items-center gap-2 text-xs">
              <Switch checked={exclude} onCheckedChange={setExclude} aria-label="Exclude selected values" />
              Exclude the selected values
            </label>
          </TabsContent>

          <TabsContent value="range" className="mt-3 flex flex-col gap-2">
            <div className="grid grid-cols-2 gap-2">
              <div className="flex flex-col gap-1">
                <Label htmlFor="filter-min" className="text-xs">
                  At least
                </Label>
                <Input
                  id="filter-min"
                  type={temporal && !measureFilter ? "date" : "text"}
                  inputMode="decimal"
                  value={min}
                  onChange={(event) => setMin(event.target.value)}
                  placeholder={domain.data?.min !== undefined ? String(domain.data.min) : "No minimum"}
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="filter-max" className="text-xs">
                  At most
                </Label>
                <Input
                  id="filter-max"
                  type={temporal && !measureFilter ? "date" : "text"}
                  inputMode="decimal"
                  value={max}
                  onChange={(event) => setMax(event.target.value)}
                  placeholder={domain.data?.max !== undefined ? String(domain.data.max) : "No maximum"}
                />
              </div>
            </div>
            {rangeInvalid ? (
              <p className="text-xs text-destructive">Enter a minimum, a maximum, or both.</p>
            ) : null}
          </TabsContent>

          <TabsContent value="relative" className="mt-3 flex flex-wrap items-end gap-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor="filter-relative" className="text-xs">
                Window
              </Label>
              <Select value={range} onValueChange={(value) => setRange(value as RelativeDateRange)}>
                <SelectTrigger id="filter-relative" className="h-9 w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RELATIVE_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {RELATIVE_OPTIONS.find((option) => option.value === range)?.counted ? (
              <div className="flex flex-col gap-1">
                <Label htmlFor="filter-count" className="text-xs">
                  N
                </Label>
                <Input
                  id="filter-count"
                  className="w-24"
                  inputMode="numeric"
                  value={count}
                  onChange={(event) => setCount(event.target.value)}
                  aria-invalid={countInvalid || undefined}
                />
              </div>
            ) : null}
          </TabsContent>
        </Tabs>

        {!measureFilter ? (
          <label className="flex items-center gap-2 text-xs">
            <Switch checked={context} onCheckedChange={setContext} aria-label="Add to context" />
            Add to context (applies before FIXED calculations)
          </label>
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={Boolean(rangeInvalid || countInvalid)}
            onClick={() => {
              onSave(build());
              onOpenChange(false);
            }}
          >
            Apply filter
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
