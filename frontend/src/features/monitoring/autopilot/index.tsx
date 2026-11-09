import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  RefreshCw,
  Settings2,
} from "lucide-react";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/auth-store";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { MetricCard } from "@/components/ui/metric-card";
import { MonitoringHeader } from "../components/monitoring-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  StateBadge,
  StatusBadge,
  type StatusTone,
} from "@/components/ui/status-badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import {
  autopilot,
  type Collection,
  type Mode,
  type Overview,
  type Policy,
  type RecordItem,
} from "./api";

const views = [
  ["overview", "Overview"],
  ["families", "Families"],
  ["incidents", "Regressions"],
  ["opportunities", "Opportunities"],
  ["experiments", "Experiments"],
  ["jobs", "Activity"],
] as const;
const human = (value?: string | null) =>
  value ? value.split("_").join(" ").toLowerCase() : "Unavailable";
const ms = (value?: number | null) =>
  value == null
    ? "Unavailable"
    : `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ms`;
const pct = (value?: number | null) =>
  value == null ? "Unavailable" : `${(value * 100).toFixed(1)}%`;
/**
 * Autopilot states the shared tone map does not know. Evidence that is
 * `unavailable` is a gap to fill, not a failure, so it stays amber here.
 */
const AUTOPILOT_TONES: Record<string, StatusTone> = {
  APPROVED: "success",
  BLOCKED: "warning",
  INCONCLUSIVE: "warning",
  READY_APPROVAL: "warning",
  expired: "warning",
  unavailable: "warning",
  REGRESSED: "danger",
  unauthorized: "danger",
};
function Status({ value }: { value?: string }) {
  if (!value) return <StatusBadge tone="neutral">Unavailable</StatusBadge>;
  return <StateBadge status={value} tone={AUTOPILOT_TONES[value]} />;
}
function useScope() {
  const user = useAuthStore((state) => state.auth.user);
  return {
    key: [
      "monitoring",
      "autopilot",
      user?.username,
      user?.activeRole,
      user?.securityContextVersion,
    ],
    admin: user?.activeRole === "ACCOUNTADMIN",
  };
}

export function QueryAutopilot({ embedded = false }: { embedded?: boolean } = {}) {
  const scope = useScope();
  const [view, setView] = useState("overview");
  const [settings, setSettings] = useState(false);
  const overview = useQuery({
    queryKey: [...scope.key, "overview"],
    queryFn: ({ signal }) => autopilot.overview(signal),
    refetchInterval: 30000,
    retry: false,
  });
  return (
    <div className="min-w-0 space-y-6">
      <MonitoringHeader
        embedded={embedded}
        title="Query Autopilot"
        description="Observe workload changes, test improvements, and review governed actions."
        actions={
          <>
            <Button
              variant="outline"
              size="icon"
              aria-label="Refresh Autopilot"
              onClick={() => void overview.refetch()}
              disabled={overview.isFetching}
            >
              <RefreshCw className="size-4" />
            </Button>
            {scope.admin && (
              <Button variant="outline" onClick={() => setSettings(true)}>
                <Settings2 className="size-4" />
                Policies
              </Button>
            )}
          </>
        }
      />
      {overview.isPending ? (
        <LoadingLines aria-label="Loading workload observations" />
      ) : overview.isError ? (
        <EmptyState
          variant="error"
          title="Autopilot is unavailable"
          description={overview.error.message}
          action={
            <Button variant="outline" onClick={() => void overview.refetch()}>
              Retry
            </Button>
          }
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <Status value={overview.data.policy.mode} />
            <span className="text-muted-foreground">
              Policy version {overview.data.policy.version}
            </span>
            <span>
              {overview.data.collection.enabled
                ? "Collection enabled"
                : "Collection paused"}
            </span>
          </div>
          <Tabs value={view} onValueChange={setView}>
            <div className="min-w-0 overflow-x-auto">
              <TabsList aria-label="Autopilot views" className="w-max">
                {views.map(([id, label]) => (
                  <TabsTrigger key={id} value={id}>
                    {label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </div>
            <TabsContent value="overview">
              <OverviewPanel data={overview.data} onView={setView} />
            </TabsContent>
            {views
              .filter(([id]) => id !== "overview")
              .map(([id, label]) => (
                <TabsContent key={id} value={id}>
                  <RecordList collection={id as Collection} label={label} />
                </TabsContent>
              ))}
          </Tabs>
          <Dialog open={settings} onOpenChange={setSettings}>
            <DialogContent className="max-h-full overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Autopilot policies</DialogTitle>
                <DialogDescription>
                  Changes invalidate approvals bound to the previous policy.
                </DialogDescription>
              </DialogHeader>
              <PolicyForm
                policy={overview.data.policy}
                onSaved={() => {
                  setSettings(false);
                  void overview.refetch();
                }}
              />
            </DialogContent>
          </Dialog>
        </>
      )}
    </div>
  );
}
function OverviewPanel({
  data,
  onView,
}: {
  data: Overview;
  onView: (view: string) => void;
}) {
  return (
    <div className="space-y-6 pt-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          ["families", "Observed families"],
          ["incidents", "Detected incidents"],
          ["opportunities", "Proposed improvements"],
          ["experiments", "Experiments"],
        ].map(([key, label]) => (
          <MetricCard
            key={key}
            weight="compact"
            label={label}
            value={data.counts[key]?.toLocaleString() ?? "Unavailable"}
          />
        ))}
      </div>
      <Card>
        <CardHeader>
          <CardTitle role="heading" aria-level={3}>
            Workload evidence
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p>
            Latency includes the Nova query lifecycle. Engine time is available
            only when a query profile supplies it.
          </p>
          <p className="text-muted-foreground">
            Regressions compare 30-minute windows with up to 14 days of history.
            At least 100 historical executions across three complete windows and
            20 recent executions are required.
          </p>
          <dl className="grid gap-3 sm:grid-cols-3">
            <div>
              <dt className="text-muted-foreground">Queued on this backend</dt>
              <dd>
                {data.collection.queued} / {data.collection.capacity}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">
                Persisted on this backend
              </dt>
              <dd>{data.collection.persisted.toLocaleString()}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Dropped observations</dt>
              <dd>{data.collection.dropped.toLocaleString()}</dd>
            </div>
          </dl>
          <Button variant="outline" onClick={() => onView("families")}>
            Inspect families
            <ArrowRight className="size-4" />
          </Button>
        </CardContent>
      </Card>
      <p className="text-sm text-muted-foreground">
        Replay requires an enrolled snapshot and explicit sample retention.
        Samples expire after {data.policy.payload_hours} hours. Approvals and
        outcomes remain in Activity.
      </p>
    </div>
  );
}
function RecordList({
  collection,
  label,
}: {
  collection: Collection;
  label: string;
}) {
  const scope = useScope();
  const [cursors, setCursors] = useState([""]);
  const [selected, setSelected] = useState<string | null>(null);
  const cursor = cursors[cursors.length - 1] ?? "";
  const query = useQuery({
    queryKey: [...scope.key, collection, cursor],
    queryFn: ({ signal }) => autopilot.list(collection, cursor, signal),
    refetchInterval: 30000,
    retry: false,
  });
  if (selected)
    return (
      <RecordDetail
        key={`${collection}:${selected}`}
        collection={collection}
        id={selected}
        onClose={() => setSelected(null)}
      />
    );
  if (query.isPending)
    return (
      <LoadingLines
        className="pt-4"
        aria-label={`Loading ${label.toLowerCase()}`}
      />
    );
  if (query.isError)
    return (
      <EmptyState
        variant="error"
        title={`Unable to load ${label.toLowerCase()}`}
        description={query.error.message}
        action={<Button onClick={() => void query.refetch()}>Retry</Button>}
      />
    );
  if (!query.data.items.length)
    return (
      <EmptyState
        icon={Activity}
        title={`No ${label.toLowerCase()} yet`}
        description={
          collection === "families"
            ? "Run queries through Nova to collect workload evidence."
            : "Records appear when sufficient evidence is collected and scheduled work runs."
        }
      />
    );
  return (
    <div className="space-y-3 pt-4">
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Workload / operation</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Historical P95</TableHead>
              <TableHead>Current P95</TableHead>
              <TableHead>Priority</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {query.data.items.map((row) => (
              <TableRow key={row.id}>
                <TableCell>
                  <Button
                    variant="link"
                    className="h-auto max-w-sm justify-start whitespace-normal p-0 text-start"
                    onClick={() => setSelected(row.id)}
                  >
                    {row.canonical ??
                      human(row.kind ?? row.detector ?? row.state)}
                    <span className="sr-only"> {row.id}</span>
                  </Button>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {row.scope
                      ? `${row.scope.principal} · ${row.scope.active_role ?? "Active role unavailable"} · ${row.scope.database}`
                      : row.id.slice(0, 16)}
                  </div>
                </TableCell>
                <TableCell>
                  <Status
                    value={
                      row.state ??
                      (row.classified === false ? "unclassified" : "observing")
                    }
                  />
                </TableCell>
                <TableCell className="tabular-nums">
                  {ms(row.baseline?.historical_p95)}
                </TableCell>
                <TableCell className="tabular-nums">
                  {ms(row.baseline?.current_p95)}
                </TableCell>
                <TableCell>{row.priority?.score ?? "Unavailable"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="flex justify-between gap-3">
        <Button
          variant="outline"
          disabled={cursors.length === 1}
          onClick={() => setCursors((v) => v.slice(0, -1))}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          disabled={!query.data.next_cursor}
          onClick={() => setCursors((v) => [...v, query.data.next_cursor!])}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
function RecordDetail({
  collection,
  id,
  onClose,
}: {
  collection: Collection;
  id: string;
  onClose: () => void;
}) {
  const scope = useScope();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: [...scope.key, collection, id],
    queryFn: ({ signal }) => autopilot.detail(collection, id, signal),
    refetchInterval: 15000,
    retry: false,
  });
  const [operationKey, setOperationKey] = useState(() => crypto.randomUUID());
  const mutation = useMutation({
    mutationFn: (operation: string) =>
      autopilot.mutate(id, operation, query.data?.version ?? 0, operationKey),
    onSuccess: () => {
      setOperationKey(crypto.randomUUID());
      toast.success("Operation recorded");
      void client.invalidateQueries({ queryKey: scope.key });
    },
  });
  const row = query.data;
  const approvalExpired =
    row?.state === "APPROVED" &&
    (!row.approval_expires_at ||
      Date.parse(row.approval_expires_at) <= Date.now());
  return (
    <section className="space-y-4 pt-4" aria-label="Autopilot record detail">
      <Button variant="ghost" onClick={onClose}>
        <ArrowLeft className="size-4" />
        Back to list
      </Button>
      {query.isPending ? (
        <LoadingLines rows={3} aria-label="Loading record" />
      ) : query.isError ? (
        <EmptyState
          variant="error"
          title="Unable to load record"
          description={query.error.message}
          action={<Button onClick={() => void query.refetch()}>Retry</Button>}
        />
      ) : (
        row && (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-base font-medium">
                {human(row.kind ?? row.detector ?? collection)}
              </h2>
              <Status value={row.state ?? row.availability ?? "observing"} />
            </div>
            {row.canonical && (
              <pre className="overflow-x-auto rounded-md border bg-muted p-3 text-xs">
                {row.canonical}
              </pre>
            )}
            {row.reason && (
              <Alert
                role="status"
                className="border-warning/30 bg-warning/10 text-warning-strong"
              >
                <AlertTriangle />
                <AlertDescription className="text-foreground">
                  {human(row.reason)}
                </AlertDescription>
              </Alert>
            )}
            {row.baseline && (
              <Card>
                <CardHeader>
                  <CardTitle role="heading" aria-level={3}>
                    Latency baseline
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <p>
                    {row.baseline.historical_count} historical executions ·{" "}
                    {row.baseline.current_count} current executions
                  </p>
                  <p>
                    Historical P95: {ms(row.baseline.historical_p95)} · Current
                    P95: {ms(row.baseline.current_p95)}
                  </p>
                  <p>
                    Historical variation envelope:{" "}
                    {ms(row.baseline.upper_envelope)}
                  </p>
                  <p>
                    {row.baseline.eligible
                      ? "Sample requirements met"
                      : human(row.baseline.reason)}
                    {row.baseline.seasonal ? " · Weekday and hour matched" : ""}
                  </p>
                </CardContent>
              </Card>
            )}
            {row.diagnosis?.map((diagnosis, i) => (
              <Card key={i}>
                <CardHeader>
                  <CardTitle role="heading" aria-level={3}>
                    {human(diagnosis.category)}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <p>{diagnosis.explanation}</p>
                  <p>Deterministic confidence: {pct(diagnosis.confidence)}</p>
                  {diagnosis.counterevidence.length > 0 && (
                    <p>
                      Counterevidence: {diagnosis.counterevidence.join(", ")}
                    </p>
                  )}
                </CardContent>
              </Card>
            ))}
            {!!row.next_steps?.length && (
              <Card>
                <CardHeader>
                  <CardTitle role="heading" aria-level={3}>
                    Next steps
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className="list-disc space-y-2 pl-5 text-sm">
                    {row.next_steps.map((step) => (
                      <li key={step}>{step}</li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}
            {row.priority && (
              <Card>
                <CardHeader>
                  <CardTitle role="heading" aria-level={3}>
                    Priority contributions
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <dl className="grid gap-2 text-sm sm:grid-cols-2">
                    {Object.entries(row.priority.contributions).map(
                      ([key, value]) => (
                        <div className="flex justify-between gap-4" key={key}>
                          <dt>{human(key)}</dt>
                          <dd className="tabular-nums">{value.toFixed(1)}</dd>
                        </div>
                      ),
                    )}
                  </dl>
                  <p className="mt-4 text-xs text-muted-foreground">
                    Expected gain is an estimate:{" "}
                    {pct(row.priority.estimated_gain)}. Source:{" "}
                    {human(row.priority.estimate_source)} (
                    {row.priority.historical_outcomes} recorded outcomes).
                  </p>
                </CardContent>
              </Card>
            )}
            {row.targets && (
              <Card>
                <CardHeader>
                  <CardTitle role="heading" aria-level={3}>
                    Proposed change
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <p>
                    {human(row.kind)} on {row.targets.join(", ")}
                  </p>
                  <p>
                    Candidate version {row.version} · Policy version{" "}
                    {row.policy_version}
                  </p>
                  {row.parameters &&
                    Object.entries(row.parameters).map(([key, value]) => (
                      <p key={key}>
                        {human(key)}: {String(value)}
                      </p>
                    ))}
                  {row.approved_by && (
                    <p>
                      Approved by {row.approved_by}; valid until{" "}
                      {row.approval_expires_at
                        ? new Date(row.approval_expires_at).toLocaleString()
                        : "unavailable"}
                      .
                    </p>
                  )}
                  {approvalExpired && (
                    <p role="status">
                      Approval expired. Run a new experiment before requesting
                      approval.
                    </p>
                  )}
                  {scope.admin && collection === "opportunities" && (
                    <div className="flex flex-wrap gap-2 pt-2">
                      {(["PROPOSED", "BLOCKED", "INCONCLUSIVE"].includes(
                        row.state ?? "",
                      ) ||
                        approvalExpired) && (
                        <Button
                          disabled={mutation.isPending}
                          onClick={() => mutation.mutate("experiment")}
                        >
                          Run sandbox experiment
                        </Button>
                      )}
                      {row.state === "READY_APPROVAL" && (
                        <Button
                          disabled={mutation.isPending}
                          onClick={() => mutation.mutate("approve")}
                        >
                          Approve this version
                        </Button>
                      )}
                      {["READY_AUTO", "APPROVED"].includes(row.state ?? "") && (
                        <Button
                          disabled={mutation.isPending || approvalExpired}
                          onClick={() => mutation.mutate("apply")}
                        >
                          Apply validated change
                        </Button>
                      )}
                      {[
                        "PROPOSED",
                        "READY_APPROVAL",
                        "APPROVED",
                        "READY_AUTO",
                        "BLOCKED",
                        "INCONCLUSIVE",
                      ].includes(row.state ?? "") && (
                        <Button
                          variant="outline"
                          disabled={mutation.isPending}
                          onClick={() => mutation.mutate("reject")}
                        >
                          Reject
                        </Button>
                      )}
                    </div>
                  )}
                  {mutation.isError && (
                    <p role="alert" className="text-destructive">
                      {mutation.error.message}
                    </p>
                  )}
                </CardContent>
              </Card>
            )}
            {row.result && (
              <Card>
                <CardHeader>
                  <CardTitle role="heading" aria-level={3}>
                    Measured experiment
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <p>
                    Correctness: {human(row.result.correctness)} ·{" "}
                    {row.result.repetitions} measured repetitions
                  </p>
                  <p>
                    Mean latency: {ms(row.result.before.mean_ms)} →{" "}
                    {ms(row.result.after.mean_ms)}
                  </p>
                  <p>
                    Standard deviation: {ms(row.result.before.stddev_ms)} →{" "}
                    {ms(row.result.after.stddev_ms)}
                  </p>
                  <p>Measured improvement: {pct(row.result.improvement)}</p>
                  <p>
                    Control mean: {ms(row.result.controls.before.mean_ms)} →{" "}
                    {ms(row.result.controls.after.mean_ms)}
                  </p>
                  {row.result.reason && <p>{human(row.result.reason)}</p>}
                </CardContent>
              </Card>
            )}
            {row.plans && (
              <Card>
                <CardHeader>
                  <CardTitle role="heading" aria-level={3}>
                    Plan comparison
                  </CardTitle>
                </CardHeader>
                <CardContent className="grid gap-4 sm:grid-cols-2">
                  {row.plan_diff && (
                    <p className="text-sm text-muted-foreground sm:col-span-2">
                      {row.plan_diff.length} operator positions changed. Costs
                      and row counts below are planner estimates.
                    </p>
                  )}
                  {(["before", "after"] as const).map((phase) => (
                    <div key={phase}>
                      <h3 className="mb-2 text-sm font-medium">
                        {phase === "before" ? "Before" : "After"}
                      </h3>
                      {row.plans?.[phase]?.operators?.length ? (
                        <ol className="space-y-2 text-xs">
                          {row.plans[phase]!.operators!.map((operator) => (
                            <li key={operator.ordinal}>
                              <strong>{operator.operator}</strong>
                              {row.plan_diff?.some(
                                (change) => change.ordinal === operator.ordinal,
                              ) && (
                                <Badge variant="outline" className="ms-2">
                                  Changed
                                </Badge>
                              )}
                              {Object.entries(operator.attributes ?? {}).map(
                                ([name, value]) => (
                                  <span key={name} className="ms-2 break-all">
                                    {name}: {value}
                                  </span>
                                ),
                              )}
                              {Object.entries(operator.estimates).map(
                                ([name, value]) => (
                                  <span
                                    key={name}
                                    className="ms-2 text-muted-foreground"
                                  >
                                    {name}: {value.toLocaleString()}
                                  </span>
                                ),
                              )}
                            </li>
                          ))}
                        </ol>
                      ) : (
                        <p className="text-sm text-muted-foreground">
                          Plan evidence unavailable
                        </p>
                      )}
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}
            {row.family_id && row.cohort_id && (
              <EvidenceList family={row.family_id} cohort={row.cohort_id} />
            )}
            {collection === "families" &&
              scope.admin &&
              row.scope?.active_role && <EnrollmentForm row={row} />}
          </>
        )
      )}
    </section>
  );
}
function EvidenceList({ family, cohort }: { family: string; cohort: string }) {
  const scope = useScope();
  const query = useQuery({
    queryKey: [...scope.key, "evidence", family, cohort],
    queryFn: ({ signal }) =>
      autopilot.related("evidence", family, cohort, signal),
    retry: false,
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle role="heading" aria-level={3}>
          Evidence availability
        </CardTitle>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <LoadingLines rows={2} aria-label="Loading evidence" />
        ) : query.isError ? (
          <EmptyState
            variant="error"
            title="Unable to load evidence"
            description={query.error.message}
          />
        ) : !query.data.items.length ? (
          <EmptyState
            title="No evidence captured"
            description="No scoped evidence has been captured for this workload."
          />
        ) : (
          <ul className="space-y-3">
            {query.data.items.map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-2 text-sm"
              >
                <div>
                  {human(item.kind)}
                  <p className="text-xs text-muted-foreground">
                    {item.reason
                      ? human(item.reason)
                      : item.expires_at
                        ? `Expires ${new Date(item.expires_at).toLocaleString()}`
                        : "No expiry"}
                  </p>
                </div>
                <Status value={item.availability} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
function PolicyForm({
  policy,
  onSaved,
}: {
  policy: Policy;
  onSaved: () => void;
}) {
  const [mode, setMode] = useState(policy.mode);
  const [enabled, setEnabled] = useState(policy.collection_enabled);
  const mutation = useMutation({
    mutationFn: () =>
      autopilot.policy({ ...policy, mode, collection_enabled: enabled }),
    onSuccess: onSaved,
  });
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="autopilot-mode">Operating mode</Label>
        <Select value={mode} onValueChange={(value) => setMode(value as Mode)}>
          <SelectTrigger id="autopilot-mode">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="OBSERVE">Observe</SelectItem>
            <SelectItem value="GOVERNED">Governed</SelectItem>
            <SelectItem value="AUTONOMOUS">Autonomous</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <p className="text-sm text-muted-foreground">
        Observe records workload only. Governed and Autonomous allow validated,
        enrolled basic statistics maintenance. Higher-risk changes require
        approval in every mode.
      </p>
      <div className="flex items-center gap-2">
        <Checkbox
          id="autopilot-collection"
          checked={enabled}
          onCheckedChange={(checked) => setEnabled(checked === true)}
        />
        <Label htmlFor="autopilot-collection" className="font-normal">
          Collect workload observations
        </Label>
      </div>
      {mutation.isError && (
        <p role="alert" className="text-sm text-destructive">
          {mutation.error.message}
        </p>
      )}
      <Button type="submit" disabled={mutation.isPending}>
        {mutation.isPending ? "Saving…" : "Save policy"}
      </Button>
    </form>
  );
}
function EnrollmentForm({ row }: { row: RecordItem }) {
  const [open, setOpen] = useState(false);
  const [sandbox, setSandbox] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [created, setCreated] = useState("");
  const [principal, setPrincipal] = useState("");
  const [role, setRole] = useState("");
  const [group, setGroup] = useState("");
  const [mapping, setMapping] = useState(
    (row.tables ?? []).map((table) => `${table}=`).join("\n"),
  );
  const [control, setControl] = useState("");
  const [optIn, setOptIn] = useState(false);
  const [autoStats, setAutoStats] = useState(false);
  const [frozen, setFrozen] = useState(false);
  const [id] = useState(() => crypto.randomUUID());
  const mutation = useMutation({
    mutationFn: () => {
      const pairs = mapping
        .split("\n")
        .filter(Boolean)
        .map((line) => line.split("=").map((s) => s.trim()));
      if (pairs.some((parts) => parts.length !== 2 || !parts[0] || !parts[1]))
        throw new Error("Map each source table to a snapshot table with =.");
      return autopilot.enroll(id, {
        id,
        version: 1,
        scope: row.scope,
        sandbox_database: sandbox,
        table_mapping: Object.fromEntries(pairs),
        snapshot_id: snapshot,
        snapshot_created_at: new Date(created).toISOString(),
        snapshot_frozen: frozen,
        execution_principal: principal,
        execution_role: role,
        budget: { resource_group: group },
        replay_opt_in: optIn,
        statistics_auto: autoStats,
        native_feedback_auto: false,
        control_families: control ? [control] : [],
        enabled: true,
      });
    },
    onSuccess: () => {
      toast.success("Snapshot enrollment saved");
      setOpen(false);
    },
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle role="heading" aria-level={3}>
          Snapshot enrollment
        </CardTitle>
      </CardHeader>
      <CardContent>
        {!open ? (
          <Button variant="outline" onClick={() => setOpen(true)}>
            Enroll this workload
          </Button>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              mutation.mutate();
            }}
          >
            <p className="text-sm text-muted-foreground">
              Register an existing immutable snapshot. The execution identity
              must already have access and a bounded resource group.
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              {[
                ["Snapshot database", sandbox, setSandbox],
                ["Snapshot provenance", snapshot, setSnapshot],
                ["Execution principal", principal, setPrincipal],
                ["Execution role", role, setRole],
                ["Resource group", group, setGroup],
                ["Control family ID", control, setControl],
              ].map(([label, value, setter]) => (
                <div key={String(label)} className="space-y-2">
                  <Label htmlFor={`enroll-${String(label)}`}>
                    {String(label)}
                  </Label>
                  <Input
                    required
                    id={`enroll-${String(label)}`}
                    value={String(value)}
                    onChange={(event) =>
                      (setter as (value: string) => void)(event.target.value)
                    }
                  />
                </div>
              ))}
              <div className="space-y-2">
                <Label htmlFor="snapshot-date">Snapshot captured at</Label>
                <Input
                  required
                  id="snapshot-date"
                  type="datetime-local"
                  value={created}
                  onChange={(event) => setCreated(event.target.value)}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="table-mapping">
                Source table = snapshot table (one pair per line)
              </Label>
              <Textarea
                required
                id="table-mapping"
                value={mapping}
                onChange={(event) => setMapping(event.target.value)}
              />
            </div>
            {(
              [
                [
                  "enroll-frozen",
                  "This snapshot will remain immutable during experiments.",
                  frozen,
                  setFrozen,
                  true,
                ],
                [
                  "enroll-replay",
                  "Retain encrypted replay samples for up to 24 hours.",
                  optIn,
                  setOptIn,
                  false,
                ],
                [
                  "enroll-statistics",
                  "Allow validated basic statistics maintenance on these tables.",
                  autoStats,
                  setAutoStats,
                  false,
                ],
              ] as const
            ).map(([fieldId, text, checked, setChecked, required]) => (
              <div key={fieldId} className="flex items-center gap-2">
                <Checkbox
                  id={fieldId}
                  required={required}
                  checked={checked}
                  onCheckedChange={(value) => setChecked(value === true)}
                />
                <Label htmlFor={fieldId} className="font-normal">
                  {text}
                </Label>
              </div>
            ))}
            {mutation.isError && (
              <p role="alert" className="text-sm text-destructive">
                {mutation.error.message}
              </p>
            )}
            <div className="flex gap-2">
              <Button disabled={mutation.isPending} type="submit">
                Save enrollment
              </Button>
              <Button
                variant="outline"
                type="button"
                onClick={() => setOpen(false)}
              >
                Cancel
              </Button>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
