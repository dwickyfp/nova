import { CircleAlert, CircleCheck, MessageSquareText } from "lucide-react";
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

  const sentence = retired
    ? "Agents no longer use this view"
    : view.active_version
      ? "Agents answer questions with this version"
      : "Agents use this view once a version is live";

  return (
    <section aria-label="At a glance" className="rounded-lg border bg-surface-1">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3 sm:px-5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <h2 className="sr-only">At a glance</h2>
          <StateBadge status={status}>
            {retired ? "Retired" : view.active_version ? `Live · version ${view.active_version}` : "Not live yet"}
          </StateBadge>
          <p className="text-sm text-muted-foreground">{sentence}</p>
        </div>
        {next ? (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm">{next.text}</p>
            <Button size="sm" onClick={onNext}>
              {next.action}
            </Button>
          </div>
        ) : null}
      </div>
      {definition.description ? (
        <p className="px-4 pt-4 text-sm leading-6 sm:px-5">{definition.description}</p>
      ) : null}
      <dl className="divide-y px-4 sm:px-5">
        <GlanceRow label="Numbers you can ask for">
          {metrics.length ? (
            <ul className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
              {metrics.slice(0, 8).map((metric) => {
                const synonyms = list<string>(metric.synonyms).slice(0, 3);
                return (
                  <li key={metric.name} className="min-w-0">
                    <span className="text-sm">{words(metric.name ?? "")}</span>
                    {synonyms.length ? (
                      <span className="block truncate text-xs text-muted-foreground">
                        Also “{synonyms.join("”, “")}”
                      </span>
                    ) : null}
                  </li>
                );
              })}
              {metrics.length > 8 ? (
                <li className="text-sm text-muted-foreground">and {metrics.length - 8} more</li>
              ) : null}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No measures defined yet</p>
          )}
        </GlanceRow>
        <GlanceRow label="Broken down by">
          {breakdowns.length ? (
            <ul className="flex flex-wrap gap-1.5">
              {breakdowns.slice(0, 12).map((name) => (
                <li key={name} className="rounded-md border bg-background px-2 py-0.5 text-sm">
                  {name}
                </li>
              ))}
              {breakdowns.length > 12 ? (
                <li className="px-1 py-0.5 text-sm text-muted-foreground">
                  and {breakdowns.length - 12} more
                </li>
              ) : null}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No breakdowns defined yet</p>
          )}
        </GlanceRow>
        <GlanceRow label="Try asking">
          {suggested.length ? (
            <ul className="space-y-2">
              {suggested.map((question) => (
                <li key={question} className="flex items-start gap-2 text-sm">
                  <MessageSquareText className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span>“{question}”</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">Add a measure first</p>
          )}
        </GlanceRow>
      </dl>
    </section>
  );
}

function GlanceRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-2 py-4 sm:grid-cols-[176px_minmax(0,1fr)] sm:gap-6">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

/** Repeated messages, once each, with how often they occur. */
export function groupMessages(messages: string[]): { text: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const message of messages) counts.set(message, (counts.get(message) ?? 0) + 1);
  return [...counts].map(([text, count]) => ({ text, count }));
}

type Validation = NonNullable<SemanticVersion["validation"]>;

/**
 * A version's check result in one line, with problems listed and repeated
 * suggestions folded into a count instead of a wall of identical lines.
 */
export function ValidationSummary({
  validation,
  acknowledged,
  onAcknowledge,
}: {
  validation: Validation;
  acknowledged: boolean;
  onAcknowledge: (value: boolean) => void;
}) {
  const errors = groupMessages(validation.errors ?? []);
  const warnings = groupMessages(validation.warnings ?? []);
  const regression = validation.regression;
  const changed = regression?.cases.filter((item) => item.status !== "matched") ?? [];
  const Icon = validation.valid ? CircleCheck : CircleAlert;
  return (
    <div className="space-y-2 text-sm">
      <p className="flex items-center gap-2">
        <Icon
          className={`size-4 shrink-0 ${validation.valid ? "text-success-strong" : "text-destructive"}`}
          aria-hidden="true"
        />
        {validation.valid
          ? "Checked: no blocking problems"
          : "Needs fixes before it can go live"}
      </p>
      {errors.length ? (
        <ul className="space-y-1 ps-6 text-destructive">
          {errors.map((item) => (
            <li key={item.text}>
              {item.text}
              {item.count > 1 ? <span className="tabular-nums"> ×{item.count}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {changed.length ? (
        <div className="space-y-1 ps-6">
          <p>
            {regression?.changed ?? changed.length} approved{" "}
            {(regression?.changed ?? changed.length) === 1 ? "example gives" : "examples give"} a
            different answer
          </p>
          <ul className="space-y-1 text-muted-foreground">
            {changed.map((item) => (
              <li key={item.verified_query_id}>
                {item.question}: {item.status === "result_changed" ? "answer changed" : item.status.replace(/_/g, " ")}
              </li>
            ))}
          </ul>
          {regression && regression.changed > 0 ? (
            <label className="flex items-center gap-2 pt-1">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(event) => onAcknowledge(event.target.checked)}
              />
              I reviewed the changed answers
            </label>
          ) : null}
        </div>
      ) : null}
      {warnings.length ? (
        <details className="ps-6">
          <summary className="cursor-pointer text-muted-foreground">
            {warnings.length} suggestion{warnings.length === 1 ? "" : "s"} to improve
          </summary>
          <ul className="mt-2 space-y-1 text-muted-foreground">
            {warnings.map((item) => (
              <li key={item.text} className="flex gap-2">
                <span className="min-w-0">{item.text}</span>
                {item.count > 1 ? (
                  <span className="shrink-0 tabular-nums">×{item.count}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
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
