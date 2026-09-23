import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  artifactFilterOperators,
  inferArtifactColumnType,
  isArtifactFilterReady,
  type ArtifactCell,
  type ArtifactFilter,
  type ArtifactFilterMode,
  type ArtifactFilterOperator,
} from "./artifact-filters";

const LABELS: Record<ArtifactFilterOperator, string> = {
  equals: "= is",
  not_equals: "!= is not",
  contains: "contains",
  not_contains: "does not contain",
  greater_than: "> greater than",
  greater_or_equal: ">= at least",
  less_than: "< less than",
  less_or_equal: "<= at most",
  between: "between",
  is_empty: "is empty",
  is_not_empty: "is not empty",
};

export function ArtifactFilterBar({
  columns,
  rows,
  filters,
  onFiltersChange,
  mode,
  onModeChange,
  visibleCount,
}: {
  columns: string[];
  rows: ArtifactCell[][];
  filters: ArtifactFilter[];
  onFiltersChange: (filters: ArtifactFilter[]) => void;
  mode: ArtifactFilterMode;
  onModeChange: (mode: ArtifactFilterMode) => void;
  visibleCount: number;
}) {
  const activeCount = filters.filter((filter) => {
    const index = columns.indexOf(filter.column);
    return (
      index >= 0 &&
      isArtifactFilterReady(filter) &&
      artifactFilterOperators(inferArtifactColumnType(rows, index)).includes(
        filter.operator,
      )
    );
  }).length;
  const update = (id: string, patch: Partial<ArtifactFilter>) =>
    onFiltersChange(
      filters.map((filter) =>
        filter.id === id ? { ...filter, ...patch } : filter,
      ),
    );

  return (
    <div className="mb-4 min-w-0 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!columns.length}
          onClick={() =>
            onFiltersChange([
              ...filters,
              {
                id: crypto.randomUUID(),
                column: columns[0],
                operator: artifactFilterOperators(
                  inferArtifactColumnType(rows, 0),
                )[0],
                value: "",
                secondValue: "",
              },
            ])
          }
          aria-label="Add filter"
        >
          <Plus aria-hidden="true" className="size-4" />
          Add filter
        </Button>
        {activeCount > 0 ? (
          <span className="text-xs text-muted-foreground" aria-live="polite">
            Showing {visibleCount.toLocaleString()} of{" "}
            {rows.length.toLocaleString()} loaded rows
          </span>
        ) : null}
        {filters.length > 0 ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onFiltersChange([])}
          >
            Clear filters
          </Button>
        ) : null}
      </div>
      {filters.length > 1 ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          Match
          <Select
            value={mode}
            onValueChange={(value) => onModeChange(value as ArtifactFilterMode)}
          >
            <SelectTrigger size="sm" aria-label="Filter match mode">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">all filters</SelectItem>
              <SelectItem value="any">any filter</SelectItem>
            </SelectContent>
          </Select>
        </div>
      ) : null}
      {filters.length > 0 ? (
        <p className="text-xs text-muted-foreground">
          Filters update this view and leave the saved SQL unchanged.
        </p>
      ) : null}
      {filters.map((filter) => {
        const index = columns.indexOf(filter.column);
        const type = index < 0 ? "text" : inferArtifactColumnType(rows, index);
        const operators = artifactFilterOperators(type);
        const needsValue =
          filter.operator !== "is_empty" && filter.operator !== "is_not_empty";
        const inputType =
          type === "number" ? "number" : type === "date" ? "date" : "text";
        return (
          <div
            key={filter.id}
            className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg border bg-card p-2"
          >
            <Select
              value={index < 0 ? undefined : filter.column}
              onValueChange={(column) => {
                const nextType = inferArtifactColumnType(
                  rows,
                  columns.indexOf(column),
                );
                update(filter.id, {
                  column,
                  operator: artifactFilterOperators(nextType)[0],
                  value: "",
                  secondValue: "",
                });
              }}
            >
              <SelectTrigger
                size="sm"
                aria-label="Filter column"
                className="max-w-full min-w-32"
              >
                <SelectValue placeholder="Choose column" />
              </SelectTrigger>
              <SelectContent>
                {columns.map((column) => (
                  <SelectItem key={column} value={column}>
                    {column}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={
                operators.includes(filter.operator)
                  ? filter.operator
                  : undefined
              }
              onValueChange={(operator) =>
                update(filter.id, {
                  operator: operator as ArtifactFilterOperator,
                  value: "",
                  secondValue: "",
                })
              }
            >
              <SelectTrigger
                size="sm"
                aria-label="Filter operator"
                className="min-w-40"
              >
                <SelectValue placeholder="Choose condition" />
              </SelectTrigger>
              <SelectContent>
                {operators.map((operator) => (
                  <SelectItem key={operator} value={operator}>
                    {LABELS[operator]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {needsValue ? (
              <Input
                type={inputType}
                step={type === "number" ? "any" : undefined}
                value={filter.value}
                onChange={(event) =>
                  update(filter.id, { value: event.target.value })
                }
                aria-label="Filter value"
                placeholder="Value"
                className="h-8 min-w-32 flex-1 sm:max-w-56"
              />
            ) : null}
            {filter.operator === "between" ? (
              <>
                <span className="text-xs text-muted-foreground">and</span>
                <Input
                  type={inputType}
                  step={type === "number" ? "any" : undefined}
                  value={filter.secondValue}
                  onChange={(event) =>
                    update(filter.id, { secondValue: event.target.value })
                  }
                  aria-label="Filter end value"
                  placeholder="End value"
                  className="h-8 min-w-32 flex-1 sm:max-w-56"
                />
              </>
            ) : null}
            {index < 0 ? (
              <span className="text-xs text-destructive">
                Column unavailable
              </span>
            ) : null}
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8"
              aria-label={`Remove filter on ${filter.column}`}
              onClick={() =>
                onFiltersChange(filters.filter((item) => item.id !== filter.id))
              }
            >
              <X aria-hidden="true" className="size-4" />
            </Button>
          </div>
        );
      })}
      {rows.length >= 500 ? (
        <p className="text-xs text-muted-foreground">
          Only the 500 loaded rows can be filtered. The query may contain more
          rows.
        </p>
      ) : null}
    </div>
  );
}
