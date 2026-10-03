import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  qualityCountBudgets,
  type QualityGateStatus,
  type QualityRun,
  type QualityScore,
  type ReleaseManifest,
} from "../quality-api";

const label = (value: string) => value.replace(/_/g, " ");
const tone = (status: QualityScore["status"]) =>
  status === "fail"
    ? "text-destructive"
    : status === "pass"
      ? "text-success-strong"
      : "text-muted-foreground";

function PromotionGateResults({ run }: { run: QualityRun }) {
  const groups = [
    ["mandatory_critical", "Mandatory critical cases"],
    ["other_quality", "Additional case and scorer gates"],
    ["performance", "Performance gates"],
  ] as const;
  const statuses: Record<QualityGateStatus, string> = {
    passed: "Passed",
    failed: "Failed",
    unavailable: "Unavailable",
    not_configured: "Not configured",
  };
  return (
    <section className="min-w-0 space-y-2" aria-label="Promotion gate results">
      <h4 className="text-sm font-medium">Promotion gate results</h4>
      {!run.gate_results && (
        <p className="text-sm text-muted-foreground">
          {run.status === "running"
            ? "Gate assessments will appear as the evaluation completes."
            : run.gates
              ? "Gate assessments are unavailable for this run."
              : "Separate gate assessments were not recorded for this legacy run."}
        </p>
      )}
      <dl className="divide-y border-y text-sm">
        {groups.map(([key, name]) => {
          const gate = run.gate_results?.[key];
          return (
            <div key={key} className="min-w-0 space-y-2 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <dt className="font-medium">{name}</dt>
                <dd className="flex flex-wrap items-center gap-2">
                  <StatusBadge
                    className={
                      gate?.status === "failed"
                        ? "bg-background dark:bg-background"
                        : undefined
                    }
                    tone={
                      gate?.status === "passed"
                        ? "success"
                        : gate?.status === "failed"
                          ? "danger"
                          : "neutral"
                    }
                  >
                    {gate
                      ? (statuses[gate.status] ?? "Unavailable")
                      : "Not recorded"}
                  </StatusBadge>
                  {gate && (
                    <span className="text-muted-foreground">
                      {gate.required ? "Required for promotion" : "Report only"}
                    </span>
                  )}
                </dd>
              </div>
              {key === "other_quality" && run.gate_results?.other_quality && (
                <dd className="break-words text-muted-foreground">
                  Case scope: {label(run.gate_results.other_quality.mode)}.
                  {run.gate_results.other_quality.required_scorers.length > 0
                    ? ` Required in every case: ${run.gate_results.other_quality.required_scorers.map(label).join(", ")}.`
                    : " No additional scorers selected."}
                </dd>
              )}
              {key === "performance" && run.gate_results?.performance && (
                <dd className="break-words text-muted-foreground">
                  {[
                    ...(run.gate_results.performance.max_latency_ms != null
                      ? [
                          `Maximum latency: ${run.gate_results.performance.max_latency_ms.toLocaleString()} ms`,
                        ]
                      : []),
                    ...qualityCountBudgets.flatMap(([count, title]) => {
                      const limit =
                        run.gate_results?.performance?.count_budgets?.[count];
                      return limit == null
                        ? []
                        : [`${title}: at most ${limit.toLocaleString()}`];
                    }),
                  ].join(" · ") ||
                    "No run-level performance limits configured."}
                </dd>
              )}
            </div>
          );
        })}
      </dl>
    </section>
  );
}

function ScoreResults({
  scores,
  performanceRequired,
}: {
  scores: QualityScore[];
  performanceRequired?: boolean;
}) {
  return (
    <ul className="space-y-3">
      {scores.map((score, index) => {
        const performance = ["latency", "efficiency"].includes(score.scorer);
        return (
          <li
            key={`${score.scorer}:${index}`}
            className="grid gap-1 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] sm:gap-4"
          >
            <p className="text-sm">
              <span className="font-medium">{label(score.scorer)}</span>{" "}
              {score.scorer_version ? (
                <span className="text-muted-foreground">
                  v{score.scorer_version}{" "}
                </span>
              ) : null}
              <span className={tone(score.status)}>{score.status}</span>
              {performance ? (
                <span className="text-muted-foreground">
                  {" · "}
                  {performanceRequired === undefined
                    ? "performance"
                    : performanceRequired && score.required
                      ? "required"
                      : "report only"}
                </span>
              ) : score.required ? (
                <span className="text-muted-foreground"> · required</span>
              ) : null}
            </p>
            <div className="min-w-0 space-y-1">
              <p className="break-words text-sm">
                {score.detail || "No explanation recorded."}
              </p>
              {score.failure_taxonomy && (
                <p className="break-words text-sm text-muted-foreground">
                  {label(score.failure_taxonomy)}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function QualityRunResults({
  run,
  caseNames = {},
  onRegression,
  onTrace,
  busy = false,
}: {
  run: QualityRun;
  caseNames?: Record<string, string>;
  onRegression?: (caseId: string) => void;
  onTrace?: (traceId: string) => void;
  busy?: boolean;
}) {
  const eligible =
    run.promotion_eligible &&
    run.status !== "running" &&
    ((!run.gate_results && !run.gates) ||
      (!!run.gate_results &&
        run.gate_results.mandatory_critical?.status === "passed" &&
        !!run.gate_results.other_quality &&
        !!run.gate_results.performance &&
        Object.values(run.gate_results).every(
          (gate) => !gate?.required || gate.status === "passed",
        )));
  return (
    <section className="min-w-0 space-y-4" aria-label="Evaluation results">
      <div className="space-y-1">
        <h3 className="text-base font-medium">
          {label(run.status)} evaluation
        </h3>
        <p className="break-all text-sm text-muted-foreground">
          Release {run.version_id} · Manifest {run.manifest_id}
          {run.scorer_set_version ? ` · Scorers ${run.scorer_set_version}` : ""}
        </p>
        <p
          role="status"
          className={eligible ? "text-sm text-success-strong" : "text-sm"}
        >
          {eligible
            ? run.gate_results
              ? "Required promotion gates passed."
              : "Legacy run is promotion eligible; separate gate assessments were not recorded."
            : "Promotion gates have not passed."}
        </p>
      </div>
      <PromotionGateResults run={run} />
      {run.results.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {run.status === "running"
            ? "Evaluation is running. Results will appear as the run completes."
            : "No case results are available for this run."}
        </p>
      ) : (
        <div className="divide-y border-y">
          {run.results.map((result) => (
            <section
              key={`${result.case_id}:${result.case_revision}`}
              className="min-w-0 space-y-3 py-4"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h4 className="min-w-0 break-words text-sm font-medium">
                  {caseNames[result.case_id] ?? result.case_id} · case revision{" "}
                  {result.case_revision}
                </h4>
                <p className="text-sm text-muted-foreground">
                  {result.duration_ms == null
                    ? "Duration unavailable"
                    : `${result.duration_ms.toLocaleString()} ms`}
                </p>
              </div>
              <ScoreResults
                scores={result.scores}
                performanceRequired={run.gate_results?.performance?.required}
              />
              {!!result.budget_scores?.length && (
                <section
                  className="space-y-2"
                  aria-label="Performance limit results"
                >
                  <h5 className="text-sm font-medium">
                    Performance limit results
                  </h5>
                  <ScoreResults
                    scores={result.budget_scores}
                    performanceRequired={
                      run.gate_results?.performance?.required
                    }
                  />
                </section>
              )}
              {(onRegression || (result.trace_id && onTrace)) && (
                <div className="flex flex-wrap gap-2">
                  {result.trace_id && onTrace && (
                    <Button
                      variant="outline"
                      className="min-h-11"
                      onClick={() => onTrace(result.trace_id!)}
                    >
                      Inspect trace
                    </Button>
                  )}
                  {onRegression &&
                    result.status !== "pass" &&
                    result.status !== "passed" && (
                      <Button
                        variant="outline"
                        className="min-h-11"
                        disabled={busy}
                        onClick={() => onRegression(result.case_id)}
                      >
                        Create regression case
                      </Button>
                    )}
                </div>
              )}
              {result.trace_id && !onTrace && (
                <p className="break-all text-xs text-muted-foreground">
                  Trace reference: {result.trace_id}
                </p>
              )}
            </section>
          ))}
        </div>
      )}
    </section>
  );
}

function tally(run: QualityRun, scorer: string) {
  const scores = run.results.flatMap((result) =>
    result.scores.filter((score) => score.scorer === scorer),
  );
  return {
    pass: scores.filter((item) => item.status === "pass").length,
    fail: scores.filter((item) => item.status === "fail").length,
    unavailable: scores.filter((item) => item.status === "unavailable").length,
  };
}

function measured(
  run: QualityRun,
  field: "duration_ms" | "tool_calls" | "total_tokens",
) {
  const values = run.results
    .map(
      (result) =>
        result[field] ??
        (field === "duration_ms"
          ? result.trace?.duration_ms
          : result.trace?.counts?.[field]),
    )
    .filter(
      (value): value is number =>
        typeof value === "number" && Number.isFinite(value),
    );
  return values.length
    ? {
        mean: values.reduce((sum, value) => sum + value, 0) / values.length,
        count: values.length,
      }
    : null;
}

export function QualityRunComparison({
  left,
  right,
}: {
  left: QualityRun;
  right: QualityRun;
}) {
  const scorers = [
    ...new Set(
      [...left.results, ...right.results].flatMap((item) =>
        item.scores.map((score) => score.scorer),
      ),
    ),
  ];
  const comparable =
    left.results.length === right.results.length &&
    left.results.every((item) =>
      right.results.some(
        (other) =>
          other.case_id === item.case_id &&
          other.case_revision === item.case_revision,
      ),
    );
  return (
    <section className="min-w-0 space-y-3" aria-label="Release comparison">
      <h3 className="text-base font-medium">Scorer comparison</h3>
      <p className="break-all text-sm text-muted-foreground">
        Baseline {left.version_id} compared with {right.version_id}
      </p>
      {!comparable && (
        <p role="status" className="text-sm text-warning-strong">
          The case sets or revisions differ. Counts describe each run; changes
          do not establish a regression.
        </p>
      )}
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">
            Pass, fail, and unavailable counts by scorer for each release
          </caption>
          <thead>
            <tr className="border-b bg-muted">
              <th className="p-3">Scorer</th>
              <th className="p-3">Baseline: pass / fail / unavailable</th>
              <th className="p-3">Compared: pass / fail / unavailable</th>
              <th className="p-3">Regressions</th>
            </tr>
          </thead>
          <tbody>
            {scorers.map((scorer) => {
              const a = tally(left, scorer),
                b = tally(right, scorer);
              return (
                <tr key={scorer} className="border-b last:border-0">
                  <th scope="row" className="p-3 font-medium">
                    {label(scorer)}
                  </th>
                  <td className="p-3 tabular-nums">
                    {a.pass} / {a.fail} / {a.unavailable}
                  </td>
                  <td className="p-3 tabular-nums">
                    {b.pass} / {b.fail} / {b.unavailable}
                  </td>
                  <td className="p-3 tabular-nums">
                    {comparable
                      ? left.results.reduce((count, result) => {
                          const before = result.scores.filter(
                            (score) => score.scorer === scorer,
                          );
                          const after =
                            right.results
                              .find(
                                (other) =>
                                  other.case_id === result.case_id &&
                                  other.case_revision === result.case_revision,
                              )
                              ?.scores.filter(
                                (score) => score.scorer === scorer,
                              ) ?? [];
                          return (
                            count +
                            before.filter(
                              (score, i) =>
                                score.status === "pass" &&
                                after[i]?.status !== "pass",
                            ).length
                          );
                        }, 0)
                      : "Not comparable"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <dl className="grid gap-4 text-sm sm:grid-cols-3">
        {(
          [
            ["duration_ms", "Mean latency (ms)"],
            ["tool_calls", "Mean tool calls"],
            ["total_tokens", "Mean tokens"],
          ] as const
        ).map(([field, name]) => {
          const a = measured(left, field),
            b = measured(right, field);
          return (
            <div key={field}>
              <dt className="text-muted-foreground">{name}</dt>
              <dd className="tabular-nums">
                {a && b && comparable
                  ? `${(b.mean - a.mean).toLocaleString(undefined, { maximumFractionDigits: 2 })} change · ${a.count} / ${b.count} measurements`
                  : "Delta unavailable"}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

function dependencyRefs(manifest: ReleaseManifest): {
  kind: string;
  name: string;
  revision: string;
  signature: string;
  external: boolean;
}[] {
  const dependencies = manifest.dependencies;
  if (!dependencies || typeof dependencies !== "object") return [];
  const groups = Array.isArray(dependencies)
    ? [["Dependency", dependencies] as const]
    : Object.entries(dependencies);
  return groups.flatMap(([kind, group]) =>
    (Array.isArray(group) ? group : [group]).flatMap((item) => {
      if (["configuration", "compiled_prompt"].includes(kind)) return [];
      const signature = JSON.stringify(item, (_key, value) =>
        value && typeof value === "object" && !Array.isArray(value)
          ? Object.fromEntries(
              Object.entries(value).sort(([a], [b]) => a.localeCompare(b)),
            )
          : value,
      );
      if (typeof item === "string")
        return [
          {
            kind: label(kind),
            name: kind === "policy_fingerprint" ? "Business policy" : item,
            revision: kind === "policy_fingerprint" ? item : "Pinned reference",
            signature,
            external: false,
          },
        ];
      if (!item || typeof item !== "object") return [];
      const ref = item as Record<string, unknown>;
      const name =
        ref.name ??
        ref.view_id ??
        ref.skill_id ??
        ref.tool_id ??
        ref.id ??
        (["compiled_instructions", "routing"].includes(kind)
          ? "Manifest snapshot"
          : undefined);
      const revision =
        ref.version ??
        ref.revision ??
        ref.fingerprint ??
        ref.digest ??
        ref.configuration_digest ??
        ref.definition_digest ??
        ref.code_revision;
      return typeof name === "string"
        ? [
            {
              kind: label(kind),
              name,
              signature,
              external: ref.externally_mutable === true,
              revision:
                typeof revision === "string" || typeof revision === "number"
                  ? String(revision)
                  : "Snapshot stored in manifest",
            },
          ]
        : [];
    }),
  );
}

export function ReleaseDependencies({
  manifest,
  baseline,
}: {
  manifest: ReleaseManifest | null;
  baseline?: ReleaseManifest | null;
}) {
  if (!manifest)
    return (
      <p className="text-sm text-muted-foreground">
        Legacy release: dependencies are unevaluated.
      </p>
    );
  const dependencies = dependencyRefs(manifest);
  const prior = baseline ? dependencyRefs(baseline) : [];
  const removed = prior.filter(
    (item) =>
      !dependencies.some(
        (current) => current.kind === item.kind && current.name === item.name,
      ),
  );
  return (
    <section className="min-w-0 space-y-3" aria-label="Release dependencies">
      <h3 className="text-sm font-medium">Pinned dependencies</h3>
      <p className="break-all text-xs text-muted-foreground">
        Manifest{" "}
        {manifest.id ??
          manifest.manifest_id ??
          manifest.fingerprint ??
          "identity unavailable"}
        {manifest.scorer_set_version ||
        (!Array.isArray(manifest.dependencies) &&
          manifest.dependencies?.scorer_set_version)
          ? ` · Scorers ${manifest.scorer_set_version ?? (!Array.isArray(manifest.dependencies) && manifest.dependencies?.scorer_set_version)}`
          : ""}
      </p>
      {dependencies.length ? (
        <ul className="divide-y border-y">
          {dependencies.map((item, index) => {
            const previous = prior.find(
              (value) => value.kind === item.kind && value.name === item.name,
            );
            return (
              <li
                key={`${item.kind}:${item.name}:${index}`}
                className="space-y-1 py-2 text-sm"
              >
                <p className="break-words">
                  {item.kind}: {item.name}
                </p>
                <p className="break-all text-xs text-muted-foreground">
                  {item.revision}
                  {baseline &&
                  (!previous || previous.signature !== item.signature)
                    ? " · changed"
                    : ""}
                  {item.external ? " · externally mutable" : ""}
                </p>
              </li>
            );
          })}
          {removed.map((item) => (
            <li
              key={`removed:${item.kind}:${item.name}`}
              className="py-2 text-sm text-muted-foreground"
            >
              {item.kind}: {item.name} · removed
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">
          No dependency summaries were returned.
        </p>
      )}
      {manifest.external_mutability?.length ? (
        <p className="text-sm text-muted-foreground">
          External behavior can change:{" "}
          {manifest.external_mutability.join(", ")}.
        </p>
      ) : null}
      {manifest.limitations?.map((limitation, index) => (
        <p key={index} className="text-sm text-muted-foreground">
          {limitation}
        </p>
      ))}
    </section>
  );
}
