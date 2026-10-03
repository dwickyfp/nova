import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import { agentVersionsApi } from "../api";
import {
  qualityApi,
  qualityBehavioralScorers,
  qualityCountBudgets,
  type PromotionGates,
  type QualityCase,
  type QualityCount,
} from "../quality-api";
import { QualityCaseEditor } from "./quality-case-editor";
import { QualityProposalDraft } from "./quality-proposal-draft";
import {
  QualityRunComparison,
  QualityRunResults,
  ReleaseDependencies,
} from "./quality-results";

export function AgentQualityTab({
  agentId,
  onInspectTrace,
}: {
  agentId: string;
  onInspectTrace?: (id: string) => void;
}) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  return (
    <QualityWorkspace
      key={`${epoch}:${agentId}`}
      agentId={agentId}
      epoch={epoch}
      onInspectTrace={onInspectTrace}
    />
  );
}

function QualityWorkspace({
  agentId,
  epoch,
  onInspectTrace,
}: {
  agentId: string;
  epoch: number;
  onInspectTrace?: (id: string) => void;
}) {
  const client = useQueryClient();
  const prefix = useId();
  const key = ["agent-quality", epoch, agentId];
  const [versionId, setVersionId] = useState("");
  const [selectedRun, setSelectedRun] = useState("");
  const [baselineRun, setBaselineRun] = useState("");
  const [otherCases, setOtherCases] =
    useState<PromotionGates["other_cases"]>("report_only");
  const [requiredScorers, setRequiredScorers] = useState<
    PromotionGates["required_scorers"]
  >([]);
  const [performance, setPerformance] =
    useState<PromotionGates["performance"]>("report_only");
  const [latencyLimit, setLatencyLimit] = useState("");
  const [countLimits, setCountLimits] = useState<
    Partial<Record<QualityCount, string>>
  >({});
  const [badInputs, setBadInputs] = useState<
    Partial<Record<QualityCount | "latency", boolean>>
  >({});
  const limits = [
    {
      name: "Maximum latency (ms)",
      value: latencyLimit,
      integer: false,
      badInput: badInputs.latency,
    },
    ...qualityCountBudgets.map(([count, name]) => ({
      name: `Maximum ${name.toLowerCase()}`,
      value: countLimits[count] ?? "",
      integer: true,
      badInput: badInputs[count],
    })),
  ];
  const invalidLimit = limits.find(({ value, integer, badInput }) => {
    if (badInput) return true;
    if (value.trim() === "") return false;
    const number = Number(value);
    return (
      !Number.isFinite(number) ||
      number < 0 ||
      number > Number.MAX_SAFE_INTEGER ||
      (integer && !Number.isSafeInteger(number))
    );
  });
  const gates: PromotionGates | undefined =
    otherCases !== "report_only" ||
    requiredScorers.length > 0 ||
    performance !== "report_only" ||
    limits.some(({ value }) => value.trim() !== "")
      ? {
          other_cases: otherCases,
          required_scorers: requiredScorers,
          performance,
          max_latency_ms:
            latencyLimit.trim() === "" ? null : Number(latencyLimit),
          count_budgets: Object.fromEntries(
            qualityCountBudgets.flatMap(([count]) => {
              const value = countLimits[count]?.trim();
              return value ? [[count, Number(value)]] : [];
            }),
          ),
        }
      : undefined;
  const [editor, setEditor] = useState<{
    initial?: QualityCase;
    regression?: boolean;
  } | null>(null);
  const cases = useQuery({
    queryKey: [...key, "cases"],
    queryFn: ({ signal }) => qualityApi.cases(agentId, signal),
    retry: false,
    gcTime: 0,
  });
  const unavailable =
    cases.isError &&
    cases.error instanceof ApiError &&
    [404, 503].includes(cases.error.status);
  const runs = useQuery({
    queryKey: [...key, "runs"],
    queryFn: ({ signal }) => qualityApi.runs(agentId, signal),
    enabled: cases.isSuccess,
    retry: false,
    gcTime: 0,
  });
  const versions = useQuery({
    queryKey: [...key, "versions"],
    queryFn: () => agentVersionsApi.list(agentId),
    enabled: cases.isSuccess,
    retry: false,
    gcTime: 0,
  });
  const activeVersion = versionId || versions.data?.active_version_id || "";
  const manifest = useQuery({
    queryKey: [...key, "manifest", activeVersion],
    queryFn: ({ signal }) =>
      qualityApi.manifest(agentId, activeVersion, signal),
    enabled: cases.isSuccess && !!activeVersion,
    retry: false,
    gcTime: 0,
  });
  const run = useQuery({
    queryKey: [...key, "run", selectedRun],
    queryFn: ({ signal }) => qualityApi.run(agentId, selectedRun, signal),
    enabled: !!selectedRun,
    retry: false,
    gcTime: 0,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 2000 : false,
  });
  const comparison = useQuery({
    queryKey: [...key, "comparison", baselineRun, selectedRun],
    queryFn: ({ signal }) =>
      qualityApi.comparison(agentId, baselineRun, selectedRun, signal),
    enabled: !!baselineRun && !!selectedRun && baselineRun !== selectedRun,
    retry: false,
    gcTime: 0,
  });
  const baselineManifest = useQuery({
    queryKey: [...key, "manifest", comparison.data?.left.version_id],
    queryFn: ({ signal }) =>
      qualityApi.manifest(agentId, comparison.data!.left.version_id, signal),
    enabled: !!comparison.data,
    retry: false,
    gcTime: 0,
  });
  const candidateManifest = useQuery({
    queryKey: [...key, "manifest", run.data?.version_id],
    queryFn: ({ signal }) =>
      qualityApi.manifest(agentId, run.data!.version_id, signal),
    enabled: !!run.data,
    retry: false,
    gcTime: 0,
  });
  const evaluate = useMutation({
    mutationFn: async () => {
      if (invalidLimit)
        throw new Error("Correct the performance limits first.");
      await qualityApi.pinManifest(agentId, activeVersion);
      return qualityApi.evaluate(agentId, activeVersion, undefined, gates);
    },
    onSuccess: (result) => {
      client.setQueryData([...key, "run", result.id], result);
      setSelectedRun(result.id);
      void client.invalidateQueries({ queryKey: key });
    },
  });
  const analyze = useMutation({
    mutationFn: () => qualityApi.analyze(agentId, selectedRun),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [...key, "proposals"] });
    },
  });
  if (cases.isPending)
    return (
      <p role="status" className="text-sm">
        Loading evaluation cases…
      </p>
    );
  if (cases.isError)
    return (
      <div role="alert" className="space-y-3">
        <p className="text-sm">
          {unavailable
            ? "Agent Quality is unavailable in this deployment. Existing knowledge and readiness checks remain available."
            : `Evaluation cases could not be loaded: ${cases.error.message}`}
        </p>
        <Button
          variant="outline"
          className="min-h-11"
          onClick={() => void cases.refetch()}
        >
          Retry Quality
        </Button>
      </div>
    );
  const caseNames = Object.fromEntries(
    (cases.data?.items ?? []).map((item) => [item.id, item.name]),
  );
  return (
    <div className="min-w-0 space-y-8">
      <section className="space-y-4">
        <div>
          <h2 className="text-base font-medium">Evaluate a release</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Pin dependencies and run the saved regression cases through the
            agent. Each scorer reports its own result.
          </p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1 space-y-2">
            <Label htmlFor={`${prefix}-version`}>Release to evaluate</Label>
            <Select
              value={activeVersion}
              onValueChange={setVersionId}
              disabled={evaluate.isPending || versions.isPending}
            >
              <SelectTrigger
                id={`${prefix}-version`}
                className="min-h-11 w-full"
              >
                <SelectValue placeholder="Select a saved version" />
              </SelectTrigger>
              <SelectContent>
                {versions.data?.versions.map((item) => (
                  <SelectItem key={item.version_id} value={item.version_id}>
                    {item.label}
                    {item.version_id === versions.data.active_version_id
                      ? " (active)"
                      : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button
            className="min-h-11"
            disabled={
              !activeVersion ||
              !cases.data?.items.length ||
              evaluate.isPending ||
              !!invalidLimit
            }
            onClick={() => evaluate.mutate()}
          >
            {evaluate.isPending ? "Evaluating release…" : "Evaluate release"}
          </Button>
        </div>
        <details className="min-w-0 rounded-md border p-4">
          <summary className="min-h-11 cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
            Promotion gates
          </summary>
          <fieldset
            disabled={evaluate.isPending}
            className="min-w-0 space-y-5 pt-3"
          >
            <legend className="sr-only">Configure promotion gates</legend>
            <p className="text-sm text-muted-foreground">
              Mandatory critical cases must pass. Additional requirements apply
              to this evaluation and are saved with its results.
            </p>
            <div className="space-y-2">
              <Label htmlFor={`${prefix}-other-cases`}>Other case gates</Label>
              <Select
                value={otherCases}
                onValueChange={(value: PromotionGates["other_cases"]) =>
                  setOtherCases(value)
                }
                disabled={evaluate.isPending}
              >
                <SelectTrigger
                  id={`${prefix}-other-cases`}
                  className="min-h-11 w-full"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="report_only">Report only</SelectItem>
                  <SelectItem value="mandatory">
                    Require other mandatory cases
                  </SelectItem>
                  <SelectItem value="all">Require all other cases</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">
                Required behavioral scorers
              </legend>
              <p className="text-sm text-muted-foreground">
                Selected scorers must pass in every case. A missing assertion or
                unavailable evidence blocks promotion.
              </p>
              <div className="grid gap-x-4 sm:grid-cols-2">
                {qualityBehavioralScorers.map((scorer) => (
                  <Label
                    key={scorer}
                    htmlFor={`${prefix}-gate-${scorer}`}
                    className="min-h-11 min-w-0 cursor-pointer gap-3 py-2 font-normal"
                  >
                    <Checkbox
                      id={`${prefix}-gate-${scorer}`}
                      checked={requiredScorers.includes(scorer)}
                      disabled={evaluate.isPending}
                      onCheckedChange={(checked) =>
                        setRequiredScorers((current) =>
                          checked === true
                            ? [...current, scorer]
                            : current.filter((value) => value !== scorer),
                        )
                      }
                    />
                    <span className="min-w-0 break-words">
                      {scorer.replace(/_/g, " ")}
                    </span>
                  </Label>
                ))}
              </div>
            </fieldset>
            <fieldset className="min-w-0 space-y-3">
              <legend className="text-sm font-medium">
                Performance limits
              </legend>
              <p
                id={`${prefix}-limits-help`}
                className="text-sm text-muted-foreground"
              >
                Leave a limit empty to omit it. Limits apply to each case;
                missing measurements are unavailable. Counts must be whole
                numbers, and all limits must be between 0 and{" "}
                {Number.MAX_SAFE_INTEGER.toLocaleString()}.
              </p>
              <div className="space-y-2">
                <Label htmlFor={`${prefix}-performance`}>
                  Performance gate mode
                </Label>
                <Select
                  value={performance}
                  onValueChange={(value: PromotionGates["performance"]) =>
                    setPerformance(value)
                  }
                  disabled={evaluate.isPending}
                >
                  <SelectTrigger
                    id={`${prefix}-performance`}
                    className="min-h-11 w-full"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="report_only">Report only</SelectItem>
                    <SelectItem value="required">
                      Required for promotion
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {performance === "required" && (
                <p className="text-sm text-muted-foreground">
                  Performance must pass using these limits or saved performance
                  assertions. Unavailable measurements block promotion.
                </p>
              )}
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="min-w-0 space-y-2">
                  <Label htmlFor={`${prefix}-latency`}>
                    Maximum latency (ms)
                  </Label>
                  <Input
                    id={`${prefix}-latency`}
                    type="number"
                    min={0}
                    max={Number.MAX_SAFE_INTEGER}
                    step="any"
                    className="min-h-11"
                    value={latencyLimit}
                    onChange={(event) => {
                      const { value } = event.target;
                      const badInput = event.target.validity.badInput;
                      setLatencyLimit(value);
                      setBadInputs((current) => ({
                        ...current,
                        latency: badInput,
                      }));
                    }}
                    aria-describedby={`${prefix}-limits-help${invalidLimit?.name === "Maximum latency (ms)" ? ` ${prefix}-limits-error` : ""}`}
                    aria-invalid={invalidLimit?.name === "Maximum latency (ms)"}
                  />
                </div>
                {qualityCountBudgets.map(([count, name]) => (
                  <div key={count} className="min-w-0 space-y-2">
                    <Label htmlFor={`${prefix}-limit-${count}`}>
                      Maximum {name.toLowerCase()}
                    </Label>
                    <Input
                      id={`${prefix}-limit-${count}`}
                      type="number"
                      min={0}
                      max={Number.MAX_SAFE_INTEGER}
                      step={1}
                      className="min-h-11"
                      value={countLimits[count] ?? ""}
                      onChange={(event) => {
                        const { value } = event.target;
                        const badInput = event.target.validity.badInput;
                        setCountLimits((current) => ({
                          ...current,
                          [count]: value,
                        }));
                        setBadInputs((current) => ({
                          ...current,
                          [count]: badInput,
                        }));
                      }}
                      aria-describedby={`${prefix}-limits-help${invalidLimit?.name === `Maximum ${name.toLowerCase()}` ? ` ${prefix}-limits-error` : ""}`}
                      aria-invalid={
                        invalidLimit?.name === `Maximum ${name.toLowerCase()}`
                      }
                    />
                  </div>
                ))}
              </div>
            </fieldset>
          </fieldset>
        </details>
        {invalidLimit && (
          <p
            id={`${prefix}-limits-error`}
            role="alert"
            className="text-sm text-destructive"
          >
            {invalidLimit.name} must be a{" "}
            {invalidLimit.integer ? "whole number" : "finite number"} between 0
            and {Number.MAX_SAFE_INTEGER.toLocaleString()}.
          </p>
        )}
        {versions.isError && (
          <p role="alert" className="text-sm text-destructive">
            Saved versions could not be loaded.{" "}
            <Button variant="link" onClick={() => void versions.refetch()}>
              Retry versions
            </Button>
          </p>
        )}
        {versions.data?.versions.length === 0 && (
          <p className="text-sm text-muted-foreground">
            Save a configuration draft to evaluate a release.
          </p>
        )}
        {evaluate.isError && (
          <p role="alert" className="text-sm text-destructive">
            {evaluate.error.message}
          </p>
        )}
        {manifest.isFetching ? (
          <p role="status" className="text-sm">
            Loading manifest…
          </p>
        ) : manifest.isError ? (
          <p role="alert" className="text-sm text-destructive">
            Manifest could not be read.{" "}
            <Button variant="link" onClick={() => void manifest.refetch()}>
              Retry manifest
            </Button>
          </p>
        ) : (
          <ReleaseDependencies manifest={manifest.data?.manifest ?? null} />
        )}
      </section>
      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-medium">Regression cases</h2>
          <Button
            variant="outline"
            className="min-h-11"
            onClick={() => setEditor({})}
          >
            Create case
          </Button>
        </div>
        {editor && (
          <QualityCaseEditor
            key={`${editor.initial?.id ?? "new"}:${editor.regression}`}
            agentId={agentId}
            epoch={epoch}
            initial={editor.initial}
            regression={editor.regression}
            onCancel={() => setEditor(null)}
            onSaved={() => setEditor(null)}
          />
        )}
        {cases.data?.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Create a case with deterministic expectations before evaluating a
            release.
          </p>
        ) : (
          <ul className="divide-y border-y">
            {cases.data?.items.map((item) => (
              <li
                key={item.id}
                className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="break-words text-sm font-medium">{item.name}</p>
                  <p className="text-sm text-muted-foreground">
                    Revision {item.revision} ·{" "}
                    {item.critical ? "critical" : "non-critical"} ·{" "}
                    {item.mandatory ? "mandatory" : "optional"} ·{" "}
                    {item.assertions.length} assertions
                  </p>
                </div>
                <Button
                  variant="outline"
                  className="min-h-11"
                  onClick={() => setEditor({ initial: item })}
                >
                  Edit {item.name}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="space-y-4">
        <h2 className="text-base font-medium">Evaluation history</h2>
        {runs.isPending ? (
          <p role="status" className="text-sm">
            Loading evaluation runs…
          </p>
        ) : runs.isError ? (
          <p role="alert" className="text-sm text-destructive">
            Runs could not be loaded.{" "}
            <Button variant="link" onClick={() => void runs.refetch()}>
              Retry runs
            </Button>
          </p>
        ) : !runs.data?.items.length ? (
          <p className="text-sm text-muted-foreground">
            No releases have been evaluated yet.
          </p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor={`${prefix}-run`}>Inspect run</Label>
              <Select value={selectedRun} onValueChange={setSelectedRun}>
                <SelectTrigger id={`${prefix}-run`} className="min-h-11 w-full">
                  <SelectValue placeholder="Select an evaluation" />
                </SelectTrigger>
                <SelectContent>
                  {runs.data.items.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.version_id} · {item.status} ·{" "}
                      {new Date(item.created_at).toLocaleString()}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${prefix}-baseline`}>
                Compare with baseline run
              </Label>
              <Select
                value={baselineRun || "none"}
                onValueChange={(value) =>
                  setBaselineRun(value === "none" ? "" : value)
                }
              >
                <SelectTrigger
                  id={`${prefix}-baseline`}
                  className="min-h-11 w-full"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">No comparison</SelectItem>
                  {runs.data.items
                    .filter((item) => item.id !== selectedRun)
                    .map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.version_id} · {item.status}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        )}
        {selectedRun && run.isPending && (
          <p role="status" className="text-sm">
            Loading scorer results…
          </p>
        )}
        {run.isError && (
          <p role="alert" className="text-sm text-destructive">
            Results could not be loaded.{" "}
            <Button variant="link" onClick={() => void run.refetch()}>
              Retry results
            </Button>
          </p>
        )}
        {run.data && !run.isError && (
          <>
            <QualityRunResults
              run={run.data}
              caseNames={caseNames}
              onTrace={onInspectTrace}
              onRegression={(id) => {
                const initial =
                  run.data?.cases?.find((item) => item.id === id) ??
                  cases.data?.items.find((item) => item.id === id);
                if (initial) setEditor({ initial, regression: true });
              }}
            />
            {candidateManifest.isPending ? (
              <p role="status" className="text-sm">
                Loading evaluated dependencies…
              </p>
            ) : candidateManifest.isError ? (
              <p role="alert" className="text-sm text-destructive">
                Evaluated dependencies could not be read.{" "}
                <Button
                  variant="link"
                  onClick={() => void candidateManifest.refetch()}
                >
                  Retry evaluated dependencies
                </Button>
              </p>
            ) : (
              <ReleaseDependencies
                manifest={candidateManifest.data?.manifest ?? null}
                baseline={
                  baselineManifest.isError
                    ? undefined
                    : baselineManifest.data?.manifest
                }
              />
            )}
            {run.data.status === "failed" && (
              <Button
                variant="outline"
                className="min-h-11"
                disabled={analyze.isPending}
                onClick={() => analyze.mutate()}
              >
                {analyze.isPending ? "Analyzing failures…" : "Analyze failures"}
              </Button>
            )}
          </>
        )}
        {analyze.isError && (
          <p role="alert" className="text-sm text-destructive">
            {analyze.error.message}
          </p>
        )}
        {analyze.isSuccess && (
          <p role="status" className="text-sm">
            A reviewable improvement proposal is available in Suggestions.
          </p>
        )}
        {comparison.isFetching && (
          <p role="status" className="text-sm">
            Comparing releases…
          </p>
        )}
        {comparison.isError && (
          <p role="alert" className="text-sm text-destructive">
            Comparison could not be loaded.{" "}
            <Button variant="link" onClick={() => void comparison.refetch()}>
              Retry comparison
            </Button>
          </p>
        )}
        {comparison.data && !comparison.isError && (
          <QualityRunComparison
            left={comparison.data.left}
            right={comparison.data.right}
          />
        )}
      </section>
      <QualityMonitoringSettings agentId={agentId} epoch={epoch} />
    </div>
  );
}

function QualityMonitoringSettings({
  agentId,
  epoch,
}: {
  agentId: string;
  epoch: number;
}) {
  const [open, setOpen] = useState(false);
  const key = ["agent-quality", epoch, agentId, "monitoring"];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => qualityApi.monitoring(agentId, signal),
    enabled: open,
    retry: false,
    gcTime: 0,
  });
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="space-y-3 border-t pt-4"
    >
      <summary className="min-h-11 cursor-pointer text-sm font-medium focus-visible:outline focus-visible:outline-ring">
        Production trace scoring
      </summary>
      {open &&
        (query.isPending ? (
          <p role="status" className="text-sm">
            Loading monitoring configuration…
          </p>
        ) : query.isError ? (
          <p role="alert" className="text-sm text-destructive">
            Monitoring settings could not be read.{" "}
            <Button variant="link" onClick={() => void query.refetch()}>
              Retry monitoring
            </Button>
          </p>
        ) : (
          query.data && (
            <MonitoringForm
              key={query.dataUpdatedAt}
              agentId={agentId}
              epoch={epoch}
              initial={query.data}
            />
          )
        ))}
    </details>
  );
}

function MonitoringForm({
  agentId,
  epoch,
  initial,
}: {
  agentId: string;
  epoch: number;
  initial: {
    enabled: boolean;
    sample_rate: number;
    max_traces: number;
    revision?: number;
    cadence_minutes?: number;
  };
}) {
  const id = useId();
  const client = useQueryClient();
  const [enabled, setEnabled] = useState(initial.enabled);
  const [rate, setRate] = useState(String(initial.sample_rate));
  const [max, setMax] = useState(String(initial.max_traces));
  const save = useMutation({
    mutationFn: () =>
      qualityApi.saveMonitoring(agentId, {
        enabled,
        sample_rate: Number(rate),
        max_traces: Number(max),
        expected_revision: initial.revision ?? 0,
        cadence_minutes: initial.cadence_minutes ?? 60,
      }),
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: ["agent-quality", epoch, agentId, "monitoring"],
      });
    },
  });
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <p className="text-sm text-muted-foreground">
        Score persisted traces under an authorized execution binding. Scoring
        does not replay tool mutations.
      </p>
      <label className="flex min-h-11 items-center gap-2 text-sm">
        <Checkbox
          checked={enabled}
          onCheckedChange={(value) => setEnabled(value === true)}
        />
        Enable production scoring
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor={`${id}-rate`}>Sampling fraction (0 to 1)</Label>
          <Input
            id={`${id}-rate`}
            type="number"
            min={0.000001}
            max={1}
            step="any"
            required
            value={rate}
            onChange={(event) => setRate(event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor={`${id}-max`}>Maximum traces per run</Label>
          <Input
            id={`${id}-max`}
            type="number"
            min={1}
            max={100}
            step={1}
            required
            value={max}
            onChange={(event) => setMax(event.target.value)}
          />
        </div>
      </div>
      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          {save.error.message}
        </p>
      )}
      <Button className="min-h-11" disabled={save.isPending} type="submit">
        {save.isPending ? "Saving monitoring…" : "Save monitoring settings"}
      </Button>
    </form>
  );
}

export function QualitySuggestions({ agentId }: { agentId: string }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  return (
    <ProposalList key={`${epoch}:${agentId}`} agentId={agentId} epoch={epoch} />
  );
}

function ProposalList({ agentId, epoch }: { agentId: string; epoch: number }) {
  const [draftProposalId, setDraftProposalId] = useState<string | null>(null);
  const client = useQueryClient();
  const key = ["agent-quality", epoch, agentId, "proposals"];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => qualityApi.proposals(agentId, signal),
    retry: false,
    gcTime: 0,
  });
  const review = useMutation({
    mutationFn: ({
      id,
      resolution,
    }: {
      id: string;
      resolution: "accepted" | "rejected";
    }) =>
      qualityApi.review(
        agentId,
        query.data!.items.find((item) => item.id === id)!,
        resolution,
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key });
    },
  });
  return (
    <section className="space-y-3">
      <h2 className="text-base font-medium">Agent improvement proposals</h2>
      <p className="text-sm text-muted-foreground">
        Review diagnosed failures and proposed changes. Accepted proposals still
        require draft evaluation and publication.
      </p>
      {query.isPending ? (
        <p role="status" className="text-sm">
          Loading proposals…
        </p>
      ) : query.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Proposals are unavailable.{" "}
          <Button variant="link" onClick={() => void query.refetch()}>
            Retry proposals
          </Button>
        </p>
      ) : !query.data?.items.length ? (
        <p className="text-sm text-muted-foreground">
          Analyze a failed evaluation to create a proposal.
        </p>
      ) : (
        <ul className="divide-y border-y">
          {query.data.items.map((item) => (
            <li key={item.id} className="min-w-0 space-y-2 py-4">
              <h3 className="break-words text-sm font-medium">
                {item.title ?? "Failure analysis"} · {item.status}
              </h3>
              {item.diagnosis && (
                <p className="break-words text-sm">
                  {item.hypothesis ? "Suspected causes: " : ""}
                  {Array.isArray(item.diagnosis)
                    ? item.diagnosis
                        .map((value) => value.replace(/_/g, " "))
                        .join(", ")
                    : item.diagnosis}
                </p>
              )}
              {item.suggestion && (
                <p className="break-words text-sm">{item.suggestion}</p>
              )}
              {item.failure_taxonomy?.length ? (
                <p className="break-words text-sm text-muted-foreground">
                  {item.failure_taxonomy
                    .map((value) => value.replace(/_/g, " "))
                    .join(", ")}
                </p>
              ) : null}
              {item.changes?.map((change, index) => (
                <p key={index} className="break-words text-sm">
                  {change.target}: {change.description}
                </p>
              ))}
              {["pending", "proposed"].includes(item.status) && (
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    className="min-h-11"
                    disabled={review.isPending}
                    onClick={() =>
                      review.mutate({ id: item.id, resolution: "rejected" })
                    }
                  >
                    Reject proposal
                  </Button>
                  <Button
                    className="min-h-11"
                    disabled={review.isPending}
                    onClick={() =>
                      review.mutate({ id: item.id, resolution: "accepted" })
                    }
                  >
                    Accept for draft
                  </Button>
                </div>
              )}
              {item.status === "accepted" && (
                <Button
                  variant="outline"
                  className="min-h-11"
                  onClick={() => setDraftProposalId(item.id)}
                >
                  Prepare configuration draft
                </Button>
              )}
              {draftProposalId === item.id && (
                <QualityProposalDraft
                  agentId={agentId}
                  epoch={epoch}
                  onClose={() => setDraftProposalId(null)}
                />
              )}
            </li>
          ))}
        </ul>
      )}
      {review.isError && (
        <p role="alert" className="text-sm text-destructive">
          {review.error.message}
        </p>
      )}
    </section>
  );
}
