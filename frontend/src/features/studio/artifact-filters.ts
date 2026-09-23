export type ArtifactCell = string | number | null;
export type ArtifactColumnType = "text" | "number" | "date";
export type ArtifactFilterOperator =
  | "equals"
  | "not_equals"
  | "contains"
  | "not_contains"
  | "greater_than"
  | "greater_or_equal"
  | "less_than"
  | "less_or_equal"
  | "between"
  | "is_empty"
  | "is_not_empty";

export type ArtifactFilter = {
  id: string;
  column: string;
  operator: ArtifactFilterOperator;
  value: string;
  secondValue: string;
};

export type ArtifactFilterMode = "all" | "any";

const NUMERIC = /^[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?$/i;
const ISO_DATE =
  /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;

export function inferArtifactColumnType(
  rows: ArtifactCell[][],
  index: number,
): ArtifactColumnType {
  const values = rows
    .map((row) => row[index])
    .filter((value) => value !== null && value !== "");
  if (!values.length) return "text";
  if (
    values.every(
      (value) =>
        typeof value === "number" || NUMERIC.test(String(value).trim()),
    )
  ) {
    return "number";
  }
  if (
    values.every(
      (value) =>
        typeof value === "string" &&
        ISO_DATE.test(value) &&
        !Number.isNaN(Date.parse(value)),
    )
  ) {
    return "date";
  }
  return "text";
}

export function artifactFilterOperators(
  type: ArtifactColumnType,
): ArtifactFilterOperator[] {
  if (type === "text") {
    return [
      "contains",
      "not_contains",
      "equals",
      "not_equals",
      "is_empty",
      "is_not_empty",
    ];
  }
  return [
    "equals",
    "not_equals",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
    "between",
    "is_empty",
    "is_not_empty",
  ];
}

export function isArtifactFilterReady(filter: ArtifactFilter): boolean {
  if (filter.operator === "is_empty" || filter.operator === "is_not_empty")
    return true;
  if (!filter.value.trim()) return false;
  return filter.operator !== "between" || Boolean(filter.secondValue.trim());
}

function comparable(
  value: ArtifactCell | string,
  type: ArtifactColumnType,
): string | number {
  if (type === "number") return Number(value);
  if (type === "date") return Date.parse(String(value).slice(0, 10));
  return String(value).toLocaleLowerCase();
}

function matches(
  value: ArtifactCell | undefined,
  filter: ArtifactFilter,
  type: ArtifactColumnType,
): boolean {
  const empty = value === null || value === undefined || value === "";
  if (filter.operator === "is_empty") return empty;
  if (filter.operator === "is_not_empty") return !empty;
  if (empty) return false;

  const actual = comparable(value, type);
  const target = comparable(filter.value, type);
  if (
    typeof actual === "number" &&
    (!Number.isFinite(actual) || !Number.isFinite(target))
  )
    return false;

  switch (filter.operator) {
    case "equals":
      return actual === target;
    case "not_equals":
      return actual !== target;
    case "contains":
      return String(actual).includes(String(target));
    case "not_contains":
      return !String(actual).includes(String(target));
    case "greater_than":
      return actual > target;
    case "greater_or_equal":
      return actual >= target;
    case "less_than":
      return actual < target;
    case "less_or_equal":
      return actual <= target;
    case "between": {
      const end = comparable(filter.secondValue, type);
      return actual >= target && actual <= end;
    }
    default:
      return false;
  }
}

export function filterArtifactRows(
  columns: string[],
  rows: ArtifactCell[][],
  filters: ArtifactFilter[],
  mode: ArtifactFilterMode,
): ArtifactCell[][] {
  const active = filters
    .filter(isArtifactFilterReady)
    .map((filter) => {
      const index = columns.indexOf(filter.column);
      if (index < 0) return null;
      const type = inferArtifactColumnType(rows, index);
      if (!artifactFilterOperators(type).includes(filter.operator)) return null;
      return { filter, index, type };
    })
    .filter((entry) => entry !== null);
  if (!active.length) return rows;
  return rows.filter((row) => {
    const results = active.map(({ filter, index, type }) =>
      matches(row[index], filter, type),
    );
    return mode === "all" ? results.every(Boolean) : results.some(Boolean);
  });
}
