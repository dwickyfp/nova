import { api } from "@/lib/api-client";

export type UsageStats = {
  activities: number;
  reported_activities: number;
  unreported_activities: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  unattributed_tokens: number;
  users: number;
  models: number;
  failed_activities: number;
  avg_tokens: number | null;
  coverage_percent: number | null;
  avg_duration_ms: number | null;
};

export type UsageGroup = UsageStats & { name: string | null };
export type UsageSource = "assistant" | "smart" | "functions";
export type UsageActivity = {
  id: string;
  at: string;
  user_name: string;
  source: UsageSource;
  action: string;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  status: string;
  duration_ms: number | null;
};

export type AIUsage = {
  start: string;
  end: string;
  previous_start: string;
  previous_end: string;
  days: number;
  summary: UsageStats;
  previous_summary: UsageStats | null;
  token_change_percent: number | null;
  daily: (UsageStats & { date: string })[];
  actions: UsageGroup[];
  models: UsageGroup[];
  users: UsageGroup[];
  sources: UsageGroup[];
  coverage: {
    source: UsageSource;
    period: "current" | "previous";
    status: "available" | "limited" | "unavailable";
    activities: number;
  }[];
  partial: boolean;
  source_limit: number;
  filters: { models: string[]; users: string[] };
  activities: UsageActivity[];
  total: number;
  offset: number;
  limit: number;
};

export type UsageFilters = {
  days: number;
  source: string;
  model: string;
  user_name: string;
  offset: number;
};

export function fetchAIUsage(filters: UsageFilters, signal?: AbortSignal) {
  const params = new URLSearchParams({
    days: String(filters.days),
    offset: String(filters.offset),
    limit: "25",
  });
  for (const name of ["source", "model", "user_name"] as const) {
    if (filters[name]) params.set(name, filters[name]);
  }
  return api.get<AIUsage>(`/monitoring/ai/usage?${params}`, signal);
}

export const sourceLabels: Record<UsageSource, string> = {
  assistant: "Nova Studio / Assistant",
  smart: "Smart Mode",
  functions: "AI Functions",
};

export function number(value: number | null) {
  return value === null
    ? "Unavailable"
    : new Intl.NumberFormat("en-US").format(value);
}

export function reportedTokens(stats: UsageStats) {
  return stats.activities > 0 && stats.reported_activities === 0
    ? "Unavailable"
    : number(stats.total_tokens);
}

export function dayLabel(date: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(date));
}

export function usageInsights(data: AIUsage): string[] {
  const insights: string[] = [];
  const action = data.actions.find((row) => row.total_tokens > 0);
  if (action) {
    insights.push(
      `${action.name} used the most reported tokens: ${number(action.total_tokens)} (${Math.round((action.total_tokens / data.summary.total_tokens) * 100)}% of the total).`,
    );
  }
  const model = data.models.find((row) => row.name && row.total_tokens > 0);
  if (model)
    insights.push(
      `${model.name} leads named models with ${number(model.total_tokens)} tokens across ${number(model.activities)} ${model.activities === 1 ? "activity" : "activities"}.`,
    );
  const peak = data.daily.reduce<(typeof data.daily)[number] | undefined>(
    (best, row) => (!best || row.total_tokens > best.total_tokens ? row : best),
    undefined,
  );
  if (peak && peak.total_tokens > 0)
    insights.push(
      `${dayLabel(peak.date)} was the busiest day by reported tokens (${number(peak.total_tokens)}).`,
    );
  if (data.summary.unreported_activities)
    insights.push(
      `${number(data.summary.unreported_activities)} ${data.summary.unreported_activities === 1 ? "activity has" : "activities have"} no token total. This consumption is not included in the reported total.`,
    );
  if (data.summary.unattributed_tokens)
    insights.push(
      `${number(data.summary.unattributed_tokens)} tokens have no recorded model attribution.`,
    );
  if (data.summary.failed_activities)
    insights.push(
      `${number(data.summary.failed_activities)} ${data.summary.failed_activities === 1 ? "activity" : "activities"} explicitly failed. Saved assistant turns do not record success or failure.`,
    );
  return insights.length
    ? insights
    : ["No reported token usage in this selection yet."];
}
