import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ResourceBindings } from "../api";

type Index = {
  name: string;
  active_version: number | null;
  filter_columns: string[];
};
type Group = { name: string; active_version: number | null };

export function AgentResourceBindings({
  value,
  onChange,
}: {
  value: ResourceBindings;
  onChange: (value: ResourceBindings) => void;
}) {
  const indexes = useQuery({
    queryKey: ["intelligence", "search"],
    queryFn: () => api.get<Index[]>("/ai/search"),
  });
  const groups = useQuery({
    queryKey: ["intelligence", "feature-groups"],
    queryFn: () => api.get<Group[]>("/features/groups"),
  });
  const selected = value.search_indexes;
  const updateFilters = (
    index: string,
    column: string,
    next: string | number | boolean | undefined,
  ) => {
    onChange({
      ...value,
      search_indexes: selected.map((item) => {
        if (item.index !== index) return item;
        const filters = { ...item.filters };
        if (next === undefined) delete filters[column];
        else filters[column] = next;
        return { ...item, filters };
      }),
    });
  };
  const names = [
    ...new Set([
      ...(indexes.data ?? [])
        .filter((item) => item.active_version)
        .map((item) => item.name),
      ...selected.map((item) => item.index),
    ]),
  ];
  const groupNames = [
    ...new Set([
      ...(groups.data ?? [])
        .filter((item) => item.active_version)
        .map((item) => item.name),
      ...value.feature_groups,
    ]),
  ];
  return (
    <div className="max-w-3xl space-y-6">
      <p className="text-sm text-muted-foreground">
        Choose the resources this agent can use. User permissions still apply.
        An empty selection grants no access through these tools. Bind Semantic
        Views in Tools.
      </p>
      <section className="space-y-3">
        <h3 className="font-medium">Search indexes</h3>
        <p className="text-sm text-muted-foreground">
          Enable AI Search in Tools to search these indexes. Fixed filters apply
          to every search and cannot be changed by the agent.
        </p>
        {indexes.isPending ? (
          <p role="status">Loading search indexes…</p>
        ) : null}
        {indexes.isError ? (
          <div role="alert">
            Could not load search indexes.{" "}
            <Button variant="outline" onClick={() => indexes.refetch()}>
              Retry search indexes
            </Button>
          </div>
        ) : null}
        {indexes.isSuccess && !names.length ? (
          <p>No published search indexes are available.</p>
        ) : null}
        {names.map((name) => {
          const binding = selected.find((item) => item.index === name);
          const definition = indexes.data?.find((item) => item.name === name);
          const columns = [
            ...new Set([
              ...(definition?.filter_columns ?? []),
              ...Object.keys(binding?.filters ?? {}),
            ]),
          ];
          return (
            <div key={name} className="space-y-3 rounded-md border p-3">
              <label className="flex min-h-11 items-center gap-3 break-all text-sm">
                <Checkbox
                  checked={!!binding}
                  onCheckedChange={(checked) =>
                    onChange({
                      ...value,
                      search_indexes: checked
                        ? [...selected, { index: name, filters: {} }]
                        : selected.filter((item) => item.index !== name),
                    })
                  }
                />
                {name}
                {!definition && !indexes.isPending ? " (unavailable)" : ""}
              </label>
              {binding
                ? columns.map((column) => {
                    const fixed = binding.filters[column];
                    const enabled = fixed !== undefined;
                    return (
                      <div
                        key={column}
                        className="flex flex-col gap-2 sm:flex-row sm:items-center"
                      >
                        <label className="flex min-h-11 items-center gap-2 text-sm sm:flex-1">
                          <Checkbox
                            checked={enabled}
                            onCheckedChange={(checked) =>
                              updateFilters(
                                name,
                                column,
                                checked ? "" : undefined,
                              )
                            }
                          />
                          Fix {column}
                        </label>
                        {enabled ? (
                          <>
                            <Select
                              value={typeof fixed}
                              onValueChange={(kind) =>
                                updateFilters(
                                  name,
                                  column,
                                  kind === "number"
                                    ? 0
                                    : kind === "boolean"
                                      ? false
                                      : String(fixed),
                                )
                              }
                            >
                              <SelectTrigger
                                aria-label={`${name} ${column} type`}
                                className="min-h-11"
                              >
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="string">Text</SelectItem>
                                <SelectItem value="number">Number</SelectItem>
                                <SelectItem value="boolean">Boolean</SelectItem>
                              </SelectContent>
                            </Select>
                            {typeof fixed === "boolean" ? (
                              <Select
                                value={String(fixed)}
                                onValueChange={(next) =>
                                  updateFilters(name, column, next === "true")
                                }
                              >
                                <SelectTrigger
                                  aria-label={`${name} ${column} value`}
                                  className="min-h-11"
                                >
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="true">True</SelectItem>
                                  <SelectItem value="false">False</SelectItem>
                                </SelectContent>
                              </Select>
                            ) : (
                              <Input
                                className="min-h-11 sm:w-48"
                                aria-label={`${name} ${column} value`}
                                type={
                                  typeof fixed === "number" ? "number" : "text"
                                }
                                value={fixed}
                                onChange={(event) =>
                                  updateFilters(
                                    name,
                                    column,
                                    typeof fixed === "number"
                                      ? Number(event.target.value)
                                      : event.target.value,
                                  )
                                }
                              />
                            )}
                          </>
                        ) : null}
                      </div>
                    );
                  })
                : null}
            </div>
          );
        })}
      </section>
      <section className="space-y-3">
        <h3 className="font-medium">Feature Groups</h3>
        <p className="text-sm text-muted-foreground">
          Enable Feature Lookup in Tools to use selected groups.
        </p>
        {groups.isPending ? <p role="status">Loading Feature Groups…</p> : null}
        {groups.isError ? (
          <div role="alert">
            Could not load Feature Groups.{" "}
            <Button variant="outline" onClick={() => groups.refetch()}>
              Retry Feature Groups
            </Button>
          </div>
        ) : null}
        {groups.isSuccess && !groupNames.length ? (
          <p>No published Feature Groups are available.</p>
        ) : null}
        {groupNames.map((name) => (
          <label
            key={name}
            className="flex min-h-11 items-center gap-3 break-all rounded-md border p-3 text-sm"
          >
            <Checkbox
              checked={value.feature_groups.includes(name)}
              onCheckedChange={(checked) =>
                onChange({
                  ...value,
                  feature_groups: checked
                    ? [...value.feature_groups, name]
                    : value.feature_groups.filter((item) => item !== name),
                })
              }
            />
            {name}
            {!groups.data?.some((item) => item.name === name) &&
            !groups.isPending
              ? " (unavailable)"
              : ""}
          </label>
        ))}
      </section>
    </div>
  );
}
