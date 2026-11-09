import { Button } from "@/components/ui/button";
import { StateBadge } from "@/components/ui/status-badge";
import type { SemanticVersion, SemanticViewDetail } from "./semantic-views-api";

/**
 * A version's status in words: a draft, checked, live for agents, replaced by
 * a newer live version, or retired with its view.
 */
export function versionStatusWord(status: string, viewRetired = false): string {
  switch (status) {
    case "DRAFT":
      return "Draft";
    case "VALIDATED":
      return "Checked";
    case "ACTIVE":
      return "Live";
    case "DEPRECATED":
      return viewRetired ? "Retired" : "Replaced";
    default:
      return status.charAt(0) + status.slice(1).toLowerCase();
  }
}

type Field = { name?: string; kind?: string; dimension?: unknown; datatype?: string; synonyms?: string[] };
type Metric = { name?: string; synonyms?: string[]; description?: string };

const NUMERIC = new Set(["integer", "int", "bigint", "decimal", "float", "double", "number", "numeric"]);

/** Turns `net_revenue` into `net revenue` for reading, not for querying. */
function words(name: string): string {
  return name.replace(/_/g, " ");
}

/**
 * The view at a glance: whether agents use it and what to do next, what can
 * be asked (measures and the words people use for them), how it can be
 * broken down, and a few example questions.
 */
export function SemanticViewGlance({
  view,
  version,
  onNext,
}: {
  view: SemanticViewDetail;
  version: SemanticVersion;
  /** Scrolls to the versions, where a draft is checked and made live. */
  onNext: () => void;
}) {
  const definition = version.definition as {
    description?: string;
    metrics?: Metric[];
    datasets?: { fields?: Field[] }[];
    verified_queries?: { question: string }[];
  };
  // Imported legacy definitions may not use lists here; read only what is.
  const list = <T,>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);
  const metrics = list<Metric>(definition.metrics).filter((item) => item && item.name);
  const breakdowns = Array.from(
    new Set(
      list<{ fields?: Field[] }>(definition.datasets).flatMap((dataset) =>
        list<Field>(dataset?.fields)
          .filter((field) => field.kind === "dimension" || field.dimension)
          // People break numbers down by names, places, categories and dates,
          // not by internal ids or other numbers.
          .filter((field) => !/(^id|_id|_no|_key|_code)$/i.test(field.name ?? ""))
          .filter((field) => !NUMERIC.has((field.datatype ?? "").toLowerCase()))
          .map((field) => words(field.name ?? "")),
      ),
    ),
  ).filter(Boolean);
  const examples = list<{ question?: string }>(definition.verified_queries)
    .map((item) => item?.question)
    .filter((question): question is string => typeof question === "string")
    .slice(0, 3);
  // With no approved examples yet, suggest questions in the shapes people ask:
  // a number by a category, a number over a period, and a top list.
  const category = breakdowns.find((name) => !/date|time|month|day|year|week/i.test(name));
  const first = metrics[0] ? words(metrics[0].name ?? "") : "";
  const second = metrics[1] ? words(metrics[1].name ?? "") : first;
  const suggested =
    examples.length || !first
      ? examples
      : [
          category ? `${first} by ${category} last month` : `${first} last month`,
          `${second} this year compared with last year`,
          ...(category ? [`top 5 ${category} by ${first}`] : []),
        ];

  const retired = view.status === "DEPRECATED";
  const latest = [...view.versions].sort((a, b) => b.version - a.version)[0];
  const next = retired
    ? null
    : latest?.status === "DRAFT" && latest.validation?.migration?.raw_definition_preserved
      ? { text: "This imported version uses an old format. Add a new version before it can go live.", action: "Go to versions" }
      : latest?.status === "DRAFT"
      ? { text: `Version ${latest.version} is a draft. Check it, then make it live.`, action: "Check the draft" }
      : latest?.status === "VALIDATED"
        ? { text: `Version ${latest.version} is checked and ready to go live.`, action: "Make it live" }
        : !view.active_version
          ? { text: "Nothing is live yet.", action: "Go to versions" }
          : null;
  const status = retired
    ? "retired"
    : view.active_version
      ? "active"
      : "draft";

  return (
    <section aria-label="At a glance" className="space-y-4 rounded-lg border bg-surface-1 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-medium">At a glance</h2>
            <StateBadge status={status}>
              {retired ? "Retired" : view.active_version ? `Live · version ${view.active_version}` : "Not live yet"}
            </StateBadge>
          </div>
          <p className="text-sm text-muted-foreground">
            {retired
              ? "Agents no longer use this view."
              : view.active_version
                ? "Agents answer questions with the live version of these definitions."
                : "Agents do not use this view until a version is live."}
          </p>
          {definition.description ? <p className="text-sm">{definition.description}</p> : null}
        </div>
        {next ? (
          <div className="flex flex-col items-start gap-2 sm:items-end">
            <p className="text-sm">{next.text}</p>
            <Button size="sm" onClick={onNext}>
              {next.action}
            </Button>
          </div>
        ) : null}
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <div className="min-w-0 space-y-2">
          <h3 className="text-sm font-medium">Numbers you can ask for</h3>
          {metrics.length ? (
            <ul className="space-y-1.5 text-sm">
              {metrics.slice(0, 8).map((metric) => (
                <li key={metric.name} className="min-w-0">
                  <span>{words(metric.name ?? "")}</span>
                  {list<string>(metric.synonyms).length ? (
                    <span className="text-muted-foreground"> · also “{list<string>(metric.synonyms).slice(0, 3).join("”, “")}”</span>
                  ) : null}
                </li>
              ))}
              {metrics.length > 8 ? (
                <li className="text-muted-foreground">and {metrics.length - 8} more</li>
              ) : null}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No measures defined yet.</p>
          )}
        </div>
        <div className="min-w-0 space-y-2">
          <h3 className="text-sm font-medium">Broken down by</h3>
          {breakdowns.length ? (
            <p className="text-sm">
              {breakdowns.slice(0, 12).join(", ")}
              {breakdowns.length > 12 ? (
                <span className="text-muted-foreground">, and {breakdowns.length - 12} more</span>
              ) : null}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">No breakdowns defined yet.</p>
          )}
        </div>
        <div className="min-w-0 space-y-2">
          <h3 className="text-sm font-medium">Try asking</h3>
          {suggested.length ? (
            <ul className="space-y-1.5 text-sm">
              {suggested.map((question) => (
                <li key={question}>“{question}”</li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">Add a measure first.</p>
          )}
        </div>
      </div>
    </section>
  );
}

/** Results of "Explore the numbers" as a table; anything else stays readable. */
export function SemanticResultTable({ result }: { result: unknown }) {
  const data = result as { columns?: unknown; rows?: unknown } | null;
  const columns = Array.isArray(data?.columns) ? (data.columns as unknown[]).map(String) : null;
  const rows = Array.isArray(data?.rows) ? (data.rows as unknown[]) : null;
  if (!columns || !rows) {
    return (
      <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
        {JSON.stringify(result, null, 2)}
      </pre>
    );
  }
  const raw = (row: unknown, index: number) =>
    Array.isArray(row) ? row[index] : (row as Record<string, unknown>)?.[columns[index]];
  const asNumber = (value: unknown) =>
    typeof value === "number" ? value
      : typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value) ? Number(value) : null;
  // A column is numeric when every value in it reads as a number.
  const numeric = columns.map((_, index) =>
    rows.length > 0 && rows.every((row) => raw(row, index) == null || asNumber(raw(row, index)) !== null));
  const cell = (row: unknown, index: number) => {
    const value = raw(row, index);
    const number = numeric[index] ? asNumber(value) : null;
    return value == null ? "—"
      : number !== null ? number.toLocaleString(undefined, { maximumFractionDigits: 2 })
        : String(value);
  };
  const heading = (column: string) => (column.split(".").pop() ?? column).replace(/_/g, " ");
  return (
    <div className="space-y-2">
      <p className="text-sm text-muted-foreground">
        {rows.length} row{rows.length === 1 ? "" : "s"}
      </p>
      <div className="max-h-96 overflow-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-muted text-left text-muted-foreground">
            <tr>
              {columns.map((column, index) => (
                <th key={column} scope="col" className={`px-3 py-2 font-medium ${numeric[index] ? "text-right" : ""}`}>
                  {heading(column)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.slice(0, 200).map((row, rowIndex) => (
              <tr key={rowIndex}>
                {columns.map((column, index) => (
                  <td key={column} className={`px-3 py-2 tabular-nums ${numeric[index] ? "text-right" : ""}`}>
                    {cell(row, index)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
