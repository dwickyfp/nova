import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Info,
  RefreshCw,
  ShieldAlert,
  Siren,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines, RefreshBanner } from "@/components/ui/loading-overlay";
import { MetricCard } from "@/components/ui/metric-card";
import { PageHeader } from "@/components/ui/page-header";
import { StatusBadge } from "@/components/ui/status-badge";
import { cn } from "@/lib/utils";
import {
  FINDING_TONE,
  OVERALL_LABEL,
  OVERALL_TONE,
  VERDICT_TONE,
  fetchAlerts,
  fetchReadiness,
  firingAlerts,
  sortAlerts,
  sortFindings,
  verdictLabel,
  type Alert,
  type AlertSeverity,
} from "./api";

const SEVERITY_ICON: Record<AlertSeverity, typeof AlertCircle> = {
  critical: Siren,
  warning: AlertTriangle,
  info: Info,
};

function AlertRow({ alert }: { alert: Alert }) {
  const Icon = SEVERITY_ICON[alert.severity];
  const tone =
    alert.status === "firing"
      ? alert.severity === "critical"
        ? "danger"
        : "warning"
      : alert.status === "unknown"
        ? "neutral"
        : "success";

  return (
    <div className="flex items-start gap-3 px-5 py-3">
      <Icon
        className={cn(
          "mt-0.5 size-4 shrink-0",
          tone === "danger"
            ? "text-destructive"
            : tone === "warning"
              ? "text-warning-strong"
              : tone === "success"
                ? "text-success-strong"
                : "text-muted-foreground",
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{alert.title}</span>
          <StatusBadge tone={tone} dot={alert.status === "firing"}>
            {alert.status === "firing"
              ? alert.severity
              : alert.status === "unknown"
                ? "no data"
                : "ok"}
          </StatusBadge>
          <span className="text-xs text-muted-foreground">
            {alert.category}
          </span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {alert.description}
        </p>
        <p className="mt-1 font-mono text-xs text-muted-foreground">
          {alert.detail} · threshold {alert.threshold}
        </p>
      </div>
    </div>
  );
}

function FindingRow({
  category,
  name,
  status,
  detail,
}: {
  category: string;
  name: string;
  status: keyof typeof FINDING_TONE;
  detail: string;
}) {
  return (
    <div className="flex items-start gap-3 px-5 py-3">
      <StatusBadge tone={FINDING_TONE[status]} className="mt-0.5">
        {status.replace("_", " ")}
      </StatusBadge>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{name}</span>
          <span className="text-xs text-muted-foreground">{category}</span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
      </div>
    </div>
  );
}

export function MonitoringHealth() {
  const alertsQuery = useQuery({
    queryKey: ["monitoring-alerts"],
    queryFn: fetchAlerts,
    refetchInterval: 30_000,
  });
  const readinessQuery = useQuery({
    queryKey: ["monitoring-readiness"],
    queryFn: fetchReadiness,
  });

  const alerts = useMemo(
    () => sortAlerts(alertsQuery.data?.alerts ?? []),
    [alertsQuery.data],
  );
  const findings = useMemo(
    () => sortFindings(readinessQuery.data?.findings ?? []),
    [readinessQuery.data],
  );

  const summary = alertsQuery.data?.summary;
  const firing = useMemo(() => firingAlerts(alerts), [alerts]);
  const isLoading = alertsQuery.isLoading || readinessQuery.isLoading;
  const isError = alertsQuery.isError || readinessQuery.isError;
  const isFetching = alertsQuery.isFetching || readinessQuery.isFetching;

  const refetchAll = () => {
    void alertsQuery.refetch();
    void readinessQuery.refetch();
  };

  return (
    <div className="relative flex flex-col gap-6">
      {isFetching && !isLoading ? <RefreshBanner label="Refreshing" /> : null}
      <PageHeader
        title="Production Health"
        description="Live alert rules and a read-only production readiness audit for this cluster."
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={refetchAll}
            disabled={isFetching}
          >
            <RefreshCw className={cn("size-4", isFetching && "animate-spin")} />
            Refresh
          </Button>
        }
      />

      {isLoading ? (
        <LoadingLines rows={6} />
      ) : isError ? (
        <EmptyState
          variant="error"
          icon={AlertCircle}
          title="Could not load production health"
          description="The engine did not answer the alert and readiness probes. The cluster may still be serving queries."
          action={
            <Button variant="outline" size="sm" onClick={refetchAll}>
              Retry
            </Button>
          }
        />
      ) : (
        <>
          <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard
              weight="compact"
              label="Overall"
              value={
                <StatusBadge
                  tone={OVERALL_TONE[summary?.overall ?? "unavailable"]}
                  dot={summary?.overall === "critical"}
                >
                  {OVERALL_LABEL[summary?.overall ?? "unavailable"]}
                </StatusBadge>
              }
              icon={ShieldAlert}
            />
            <MetricCard
              weight="compact"
              label="Critical alerts"
              value={summary?.counts.critical ?? 0}
              icon={Siren}
            />
            <MetricCard
              weight="compact"
              label="Warnings"
              value={summary?.counts.warning ?? 0}
              icon={AlertTriangle}
            />
            <MetricCard
              weight="compact"
              label="No data"
              value={summary?.unknown ?? 0}
              icon={Info}
            />
          </section>

          <section className="rounded-xl border border-border bg-surface-2">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-5 py-3">
              <h3 className="text-sm font-medium">Alert rules</h3>
              <span className="text-xs text-muted-foreground">
                {firing.length} firing of {alerts.length}
              </span>
            </div>
            <div className="divide-y divide-border">
              {alerts.map((alert) => (
                <AlertRow key={alert.id} alert={alert} />
              ))}
            </div>
          </section>

          <section className="rounded-xl border border-border bg-surface-2">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-5 py-3">
              <div className="flex items-center gap-2">
                <ClipboardCheck className="size-4 text-muted-foreground" />
                <h3 className="text-sm font-medium">
                  Production readiness audit
                </h3>
              </div>
              <StatusBadge
                tone={
                  VERDICT_TONE[readinessQuery.data?.result ?? ""] ?? "neutral"
                }
              >
                {verdictLabel(readinessQuery.data?.result ?? "")}
              </StatusBadge>
            </div>
            <div className="divide-y divide-border">
              {findings.map((finding, index) => (
                <FindingRow
                  key={`${finding.category}-${finding.name}-${index}`}
                  category={finding.category}
                  name={finding.name}
                  status={finding.status}
                  detail={finding.detail}
                />
              ))}
            </div>
            {findings.length > 0 ? (
              <div className="flex items-start gap-2 border-t border-border px-5 py-3 text-xs text-muted-foreground">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
                <span>
                  This audit is read-only. REVIEW findings are gates Nova cannot
                  prove from inside the engine (backup restore, failure domains,
                  alert delivery) and must be verified separately.
                </span>
              </div>
            ) : null}
          </section>
        </>
      )}
    </div>
  );
}
