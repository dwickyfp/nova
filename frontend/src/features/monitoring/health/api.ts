import { api } from "@/lib/api-client";

export type AlertStatus = "firing" | "ok" | "unknown";
export type AlertSeverity = "critical" | "warning" | "info";

export type Alert = {
  id: string;
  title: string;
  category: string;
  severity: AlertSeverity;
  status: AlertStatus;
  value: number | string | null;
  threshold: string;
  description: string;
  detail: string;
};

export type AlertSummary = {
  overall: "critical" | "warning" | "degraded" | "healthy" | "unavailable";
  firing: number;
  unknown: number;
  counts: Record<string, number>;
};

export type AlertsResponse = {
  summary: AlertSummary;
  alerts: Alert[];
};

export type FindingStatus = "PASS" | "NEEDS_CHANGE" | "BLOCKED" | "REVIEW";

export type Finding = {
  category: string;
  name: string;
  status: FindingStatus;
  detail: string;
  node?: string;
};

export type ReadinessResponse = {
  metadata: {
    cluster_id: string | null;
    version: string | null;
    generated_at: string;
    engine_reachable: boolean;
  };
  findings: Finding[];
  counts: Record<string, number>;
  result: string;
};

export function fetchAlerts() {
  return api.get<AlertsResponse>("/monitoring/alerts");
}

export function fetchReadiness() {
  return api.get<ReadinessResponse>("/monitoring/readiness");
}

/** Severity rank for sorting: critical first, then warning, then info. */
export const SEVERITY_RANK: Record<AlertSeverity, number> = {
  critical: 0,
  warning: 1,
  info: 2,
};

/**
 * Sort alerts so the ones an operator must look at first are first: firing
 * before ok, critical before warning, then by category for stability. Unknown
 * (unproven) sits between firing and ok — it is not a pass.
 */
export function sortAlerts(alerts: Alert[]): Alert[] {
  const statusRank: Record<AlertStatus, number> = {
    firing: 0,
    unknown: 1,
    ok: 2,
  };
  return [...alerts].sort((a, b) => {
    if (statusRank[a.status] !== statusRank[b.status]) {
      return statusRank[a.status] - statusRank[b.status];
    }
    if (SEVERITY_RANK[a.severity] !== SEVERITY_RANK[b.severity]) {
      return SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
    }
    if (a.category !== b.category) return a.category.localeCompare(b.category);
    return a.title.localeCompare(b.title);
  });
}

/** Only firing alerts; used by the summary strip. */
export function firingAlerts(alerts: Alert[]): Alert[] {
  return alerts.filter((alert) => alert.status === "firing");
}

export const OVERALL_TONE: Record<
  AlertSummary["overall"],
  "success" | "warning" | "danger" | "neutral"
> = {
  healthy: "success",
  warning: "warning",
  critical: "danger",
  degraded: "warning",
  unavailable: "neutral",
};

export const OVERALL_LABEL: Record<AlertSummary["overall"], string> = {
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  degraded: "Degraded",
  unavailable: "Unavailable",
};

export const FINDING_TONE: Record<
  FindingStatus,
  "success" | "warning" | "danger" | "neutral"
> = {
  PASS: "success",
  NEEDS_CHANGE: "warning",
  BLOCKED: "danger",
  REVIEW: "neutral",
};

/** Human label for the overall audit verdict. */
export function verdictLabel(result: string): string {
  switch (result) {
    case "PRODUCTION_ACCEPTANCE_PASSED":
      return "Production ready";
    case "PRODUCTION_ACCEPTANCE_PENDING":
      return "Pending review";
    case "PRODUCTION_ACCEPTANCE_NEEDS_CHANGE":
      return "Needs change";
    case "PRODUCTION_ACCEPTANCE_BLOCKED":
      return "Blocked";
    default:
      return result;
  }
}

export const VERDICT_TONE: Record<
  string,
  "success" | "warning" | "danger" | "neutral"
> = {
  PRODUCTION_ACCEPTANCE_PASSED: "success",
  PRODUCTION_ACCEPTANCE_PENDING: "neutral",
  PRODUCTION_ACCEPTANCE_NEEDS_CHANGE: "warning",
  PRODUCTION_ACCEPTANCE_BLOCKED: "danger",
};

/** Findings ordered worst-first, then by category for stable rendering. */
export function sortFindings(findings: Finding[]): Finding[] {
  const rank: Record<FindingStatus, number> = {
    BLOCKED: 0,
    NEEDS_CHANGE: 1,
    REVIEW: 2,
    PASS: 3,
  };
  return [...findings].sort((a, b) => {
    if (rank[a.status] !== rank[b.status])
      return rank[a.status] - rank[b.status];
    if (a.category !== b.category) return a.category.localeCompare(b.category);
    return a.name.localeCompare(b.name);
  });
}
