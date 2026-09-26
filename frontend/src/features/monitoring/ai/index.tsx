import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ApiError } from "@/lib/api-client";
import { readModeToken } from "@/lib/read-token";
import { useAuthStore } from "@/stores/auth-store";
import { useTheme } from "@/context/theme-provider";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  dayLabel,
  fetchAIUsage,
  number,
  reportedTokens,
  sourceLabels,
  usageInsights,
  type AIUsage,
  type UsageFilters,
  type UsageGroup,
} from "./api";

const initialFilters: UsageFilters = {
  days: 7,
  source: "",
  model: "",
  user_name: "",
  offset: 0,
};

function Panel({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="min-w-0 rounded-xl border bg-card">
      <div className="px-4 pt-4 sm:px-5 sm:pt-5">
        <h2 className="text-sm font-medium">{title}</h2>
        {description && (
          <p className="mt-1 text-xs text-muted-foreground">{description}</p>
        )}
      </div>
      <div className="min-w-0 p-4 sm:p-5">{children}</div>
    </section>
  );
}

function Filter({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <div className="min-w-0 space-y-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <Select
        value={value || "__all__"}
        onValueChange={(next) => onChange(next === "__all__" ? "" : next)}
      >
        <SelectTrigger
          aria-label={label}
          className="min-h-11 w-full min-w-0 [&>span]:truncate"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem
              key={option.value}
              value={option.value}
              className="min-h-11"
            >
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function TokenTrend({ data }: { data: AIUsage }) {
  const { resolvedTheme } = useTheme();
  const colors = {
    bar: readModeToken("--chart-1", resolvedTheme, "currentColor"),
    text: readModeToken("--muted-foreground", resolvedTheme, "currentColor"),
    border: readModeToken("--border", resolvedTheme, "currentColor"),
  };
  return (
    <Panel
      title="Daily token usage"
      description="Reported totals by UTC date. Today is still in progress."
    >
      {data.summary.reported_activities === 0 ? (
        <EmptyState
          title="No reported tokens"
          description="Activities without token metadata are listed below. Their usage is unavailable."
        />
      ) : (
        <div
          className="aspect-[4/3] min-w-0 sm:aspect-[5/2]"
          role="img"
          aria-label="Daily reported token usage. Exact values are available in the daily breakdown below."
        >
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <BarChart
              data={data.daily.map((day) => ({
                ...day,
                tokens:
                  day.reported_activities || !day.activities
                    ? day.total_tokens
                    : null,
              }))}
              margin={{ left: 0, right: 0, top: 8, bottom: 0 }}
              accessibilityLayer
            >
              <CartesianGrid vertical={false} stroke={colors.border} />
              <XAxis
                dataKey="date"
                tickFormatter={dayLabel}
                tick={{ fill: colors.text, fontSize: 12 }}
                tickLine={false}
                axisLine={false}
                minTickGap={24}
              />
              <YAxis
                width={45}
                tick={{ fill: colors.text, fontSize: 12 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(value: number) =>
                  Intl.NumberFormat("en-US", { notation: "compact" }).format(
                    value,
                  )
                }
              />
              <Tooltip
                labelFormatter={(label) => dayLabel(String(label))}
                formatter={(value) => [
                  number(Number(value)),
                  "Reported tokens",
                ]}
                contentStyle={{
                  background: "var(--popover)",
                  color: "var(--popover-foreground)",
                  borderColor: "var(--border)",
                  borderRadius: "var(--radius)",
                }}
                cursor={{ fill: "var(--muted)" }}
              />
              <Bar
                dataKey="tokens"
                name="Reported tokens"
                fill={colors.bar}
                maxBarSize={32}
                isAnimationActive={false}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <details className="mt-4">
        <summary className="cursor-pointer rounded-md py-3 text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">
          Daily breakdown
        </summary>
        <Table aria-label="Daily token breakdown">
          <TableHeader>
            <TableRow>
              <TableHead>Date (UTC)</TableHead>
              <TableHead className="text-right">Input</TableHead>
              <TableHead className="text-right">Output</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead className="text-right">Activities</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.daily.map((day) => (
              <TableRow key={day.date}>
                <TableCell>{dayLabel(day.date)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {day.reported_activities
                    ? number(day.input_tokens)
                    : day.activities
                      ? "Unavailable"
                      : "0"}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {day.reported_activities
                    ? number(day.output_tokens)
                    : day.activities
                      ? "Unavailable"
                      : "0"}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {reportedTokens(day)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {number(day.activities)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </details>
    </Panel>
  );
}

function Ranking({
  title,
  rows,
  onSelect,
  label,
}: {
  title: string;
  rows: UsageGroup[];
  onSelect: (name: string) => void;
  label: string;
}) {
  const ranked = rows
    .filter((row) => row.name && row.total_tokens > 0)
    .slice(0, 5);
  return (
    <Panel
      title={title}
      description="Top 5 by reported tokens. Select a name to filter the dashboard."
    >
      {ranked.length === 0 ? (
        <p className="py-4 text-sm text-muted-foreground">
          No attributed token usage in this selection.
        </p>
      ) : (
        <ol className="divide-y">
          {ranked.map((row, index) => (
            <li key={row.name} className="flex min-w-0 items-center gap-3 py-2">
              <span className="text-xs tabular-nums text-muted-foreground">
                {index + 1}
              </span>
              <Button
                variant="link"
                className="min-h-11 min-w-0 flex-1 justify-start px-0 text-left text-foreground"
                aria-label={`Filter by ${label} ${row.name}`}
                onClick={() => onSelect(row.name!)}
              >
                <span className="truncate">{row.name}</span>
              </Button>
              <div className="shrink-0 text-right text-sm tabular-nums">
                {number(row.total_tokens)}
                <p className="text-xs text-muted-foreground">
                  {number(row.activities)} activities
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

export function AIUsageDashboard({
  data,
  setFilter,
}: {
  data: AIUsage;
  setFilter: (key: keyof UsageFilters, value: string | number) => void;
}) {
  const { summary } = data;
  const measured = summary.reported_activities > 0;
  const change = data.token_change_percent;
  return (
    <>
      {data.partial && (
        <div role="alert" className="rounded-lg border p-4 text-sm">
          <p className="font-medium">Incomplete history</p>
          <p className="mt-1 text-muted-foreground">
            Totals cover the available records only. Period comparison is
            disabled. A source may be unavailable or exceed{" "}
            {number(data.source_limit)} records; see data coverage below.
          </p>
        </div>
      )}
      <section
        aria-label="Usage totals"
        className="rounded-xl border bg-surface-2"
      >
        <dl className="grid min-w-0 gap-6 p-5 sm:grid-cols-2 @3xl/ai:grid-cols-4">
          <div className="min-w-0">
            <dt className="text-sm text-muted-foreground">Reported tokens</dt>
            <dd className="mt-2 break-words text-3xl tabular-nums sm:text-4xl">
              {reportedTokens(summary)}
            </dd>
            <dd className="mt-2 text-xs text-muted-foreground">
              {measured && change !== null
                ? `${change > 0 ? "+" : ""}${change}% vs previous ${data.days} days`
                : "No comparable previous token total"}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-muted-foreground">Input / output</dt>
            <dd className="mt-2 text-lg tabular-nums">
              {measured
                ? `${number(summary.input_tokens)} / ${number(summary.output_tokens)}`
                : summary.activities
                  ? "Unavailable"
                  : "0 / 0"}
            </dd>
            <dd className="mt-2 text-xs text-muted-foreground">
              Available token components
            </dd>
          </div>
          <div>
            <dt className="text-sm text-muted-foreground">
              Observed activities
            </dt>
            <dd className="mt-2 text-2xl tabular-nums">
              {number(summary.activities)}
            </dd>
            <dd className="mt-2 text-xs text-muted-foreground">
              {number(summary.users)} {summary.users === 1 ? "user" : "users"} ·{" "}
              {number(summary.models)} named{" "}
              {summary.models === 1 ? "model" : "models"}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-muted-foreground">Token coverage</dt>
            <dd className="mt-2 text-2xl tabular-nums">
              {summary.coverage_percent === null
                ? "No activity"
                : `${summary.coverage_percent}%`}
            </dd>
            <dd className="mt-2 text-xs text-muted-foreground">
              {number(summary.reported_activities)} of{" "}
              {number(summary.activities)} activities report a total
            </dd>
          </div>
        </dl>
      </section>
      <div className="grid min-w-0 items-start gap-5 @3xl/ai:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <TokenTrend data={data} />
        <Panel
          title="Usage summary"
          description="Calculated from the selected history."
        >
          <ul className="space-y-4 text-sm leading-relaxed">
            {usageInsights(data).map((text) => (
              <li key={text} className="break-words">
                {text}
              </li>
            ))}
          </ul>
          {summary.avg_tokens !== null && (
            <p className="mt-5 border-t pt-4 text-xs text-muted-foreground">
              Average: {number(summary.avg_tokens)} tokens per activity with a
              reported total.
            </p>
          )}
        </Panel>
      </div>
      <Panel
        title="Usage by action"
        description="An activity is a saved assistant turn, a Smart run, or an AI SQL statement. It can contain several provider calls."
      >
        {data.actions.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No actions in this selection.
          </p>
        ) : (
          <Table aria-label="Token usage by action">
            <TableHeader>
              <TableRow>
                <TableHead>Action</TableHead>
                <TableHead className="text-right">Activities</TableHead>
                <TableHead className="text-right">Reported tokens</TableHead>
                <TableHead className="text-right">Avg. tokens</TableHead>
                <TableHead className="text-right">Missing totals</TableHead>
                <TableHead className="text-right">Failed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.actions.map((row) => (
                <TableRow key={row.name}>
                  <TableCell className="font-medium">{row.name}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {number(row.activities)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {reportedTokens(row)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {number(row.avg_tokens)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {number(row.unreported_activities)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.name?.includes("Assistant") ||
                    row.name === "Workspace assistant"
                      ? "Not recorded"
                      : number(row.failed_activities)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Panel>
      <div className="grid min-w-0 gap-5 @3xl/ai:grid-cols-2">
        <Ranking
          title="Top models"
          rows={data.models}
          label="model"
          onSelect={(name) => setFilter("model", name)}
        />
        <Ranking
          title="Top users"
          rows={data.users}
          label="user"
          onSelect={(name) => setFilter("user_name", name)}
        />
      </div>
      <Panel
        title="Activity history"
        description="Most recent first. All timestamps use UTC. Unavailable token counts are excluded from totals."
      >
        <Table aria-label="AI activity history">
          <TableHeader>
            <TableRow>
              {[
                "Time (UTC)",
                "Action",
                "User",
                "Model",
                "Input",
                "Output",
                "Total",
                "Status",
              ].map((title) => (
                <TableHead key={title}>{title}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.activities.map((row) => (
              <TableRow key={row.id}>
                <TableCell className="text-xs">
                  {new Date(row.at)
                    .toISOString()
                    .replace("T", " ")
                    .slice(0, 19)}
                </TableCell>
                <TableCell>{row.action}</TableCell>
                <TableCell>{row.user_name}</TableCell>
                <TableCell>{row.model || "Unattributed"}</TableCell>
                <TableCell className="tabular-nums">
                  {number(row.input_tokens)}
                </TableCell>
                <TableCell className="tabular-nums">
                  {number(row.output_tokens)}
                </TableCell>
                <TableCell className="tabular-nums">
                  {number(row.total_tokens)}
                </TableCell>
                <TableCell>
                  {row.status === "recorded" ? "Saved turn" : row.status}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {data.activities.length === 0 && (
          <p className="py-4 text-sm text-muted-foreground">
            No activities on this page.
          </p>
        )}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            {data.activities.length
              ? `${number(data.offset + 1)} to ${number(Math.min(data.offset + data.activities.length, data.total))} of ${number(data.total)} activities`
              : `${number(data.total)} activities in this selection`}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              className="min-h-11"
              disabled={data.offset === 0}
              onClick={() =>
                setFilter("offset", Math.max(0, data.offset - data.limit))
              }
            >
              Previous
            </Button>
            <Button
              variant="outline"
              className="min-h-11"
              disabled={data.offset + data.limit >= data.total}
              onClick={() => setFilter("offset", data.offset + data.limit)}
            >
              Next
            </Button>
          </div>
        </div>
      </Panel>
      <Panel
        title="Data coverage & definitions"
        description="Use this dashboard to monitor recorded activity. It is not a billing statement."
      >
        <div className="space-y-3 text-sm leading-relaxed text-muted-foreground">
          <p>
            AI Functions appear as SQL statements. Their token usage and model
            are unavailable because the current SQL execution path does not save
            that metadata. Failed SQL can stop before a provider call.
          </p>
          <p>
            Smart coordinator and specialist runs are counted separately; the
            final message total is excluded to prevent duplicates. Smart tokens
            use the run start date. In-progress counters can increase, and model
            attribution is unavailable.
          </p>
          <p>
            Assistant totals come from retained messages. Deleted history is no
            longer included. Model names reflect the saved turn and may not
            describe every nested call. AI Search embeddings, decision routing,
            and provider connection tests are not independently metered here.
          </p>
          <p>
            The current period includes today so far. Comparison ends at the
            same UTC time in the previous period. Missing usage is not
            estimated, and monetary cost is not calculated.
          </p>
        </div>
        <details className="mt-4">
          <summary className="cursor-pointer rounded-md py-3 text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">
            Source availability
          </summary>
          <Table aria-label="AI usage source availability">
            <TableHeader>
              <TableRow>
                <TableHead>Source</TableHead>
                <TableHead>Period</TableHead>
                <TableHead>History</TableHead>
                <TableHead className="text-right">Activities</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.coverage.map((row) => (
                <TableRow key={`${row.source}-${row.period}`}>
                  <TableCell>{sourceLabels[row.source]}</TableCell>
                  <TableCell>{row.period}</TableCell>
                  <TableCell>{row.status}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.status === "unavailable"
                      ? "Unavailable"
                      : number(row.activities)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <p className="mt-3 text-xs text-muted-foreground">
            Up to {number(data.source_limit)} history records per source and
            period. Source counts are before dashboard filters.
          </p>
        </details>
      </Panel>
    </>
  );
}

export function MonitoringAI() {
  const [filters, setFilters] = useState<UsageFilters>(initialFilters);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const user = useAuthStore((state) => state.auth.user);
  const query = useQuery({
    queryKey: ["monitoring", "ai", user?.username, user?.activeRole, filters],
    queryFn: ({ signal }) => fetchAIUsage(filters, signal),
    staleTime: 30_000,
    refetchInterval: autoRefresh ? 60_000 : false,
    retry: false,
  });
  const data = query.data;
  const restricted =
    query.error instanceof ApiError && query.error.status === 403;
  const setFilter = (key: keyof UsageFilters, value: string | number) => {
    setFilters((current) => ({
      ...current,
      [key]: value,
      offset: key === "offset" ? Number(value) : 0,
    }));
  };
  const filtered = Boolean(
    filters.source || filters.model || filters.user_name,
  );
  return (
    <div
      className="@container/ai flex min-w-0 flex-col gap-5"
      aria-busy={query.isFetching}
    >
      <PageHeader
        title="AI Monitoring"
        description="Track token usage across Nova Studio, assistant turns, Smart Mode, and AI Functions."
        actions={
          <Button
            variant="outline"
            className="min-h-11"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <RefreshCw className="size-4" aria-hidden="true" />
            {query.isFetching ? "Refreshing" : "Refresh"}
          </Button>
        }
      />
      <div className="grid min-w-0 gap-3 sm:grid-cols-2 @3xl/ai:grid-cols-4">
        <Filter
          label="Period"
          value={String(filters.days)}
          onChange={(value) => setFilter("days", Number(value))}
          options={[
            { value: "1", label: "Today" },
            { value: "7", label: "Last 7 days" },
            { value: "14", label: "Last 14 days" },
            { value: "30", label: "Last 30 days" },
          ]}
        />
        <Filter
          label="Source"
          value={filters.source}
          onChange={(value) => setFilter("source", value)}
          options={[
            { value: "__all__", label: "All sources" },
            ...Object.entries(sourceLabels).map(([value, label]) => ({
              value,
              label,
            })),
          ]}
        />
        <Filter
          label="Model"
          value={filters.model}
          onChange={(value) => setFilter("model", value)}
          options={[
            { value: "__all__", label: "All models" },
            ...Array.from(
              new Set([
                ...(data?.filters.models ?? []),
                ...(filters.model ? [filters.model] : []),
              ]),
            ).map((value) => ({ value, label: value })),
          ]}
        />
        <Filter
          label="User"
          value={filters.user_name}
          onChange={(value) => setFilter("user_name", value)}
          options={[
            { value: "__all__", label: "All users" },
            ...Array.from(
              new Set([
                ...(data?.filters.users ?? []),
                ...(filters.user_name ? [filters.user_name] : []),
              ]),
            ).map((value) => ({ value, label: value })),
          ]}
        />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <p>
          {data
            ? `${dayLabel(data.start)} to ${dayLabel(data.end)} · UTC · Updated ${new Date(data.end).toISOString().slice(11, 19)}`
            : "Dates and times use UTC"}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="ghost"
            className="min-h-11 text-xs"
            aria-pressed={autoRefresh}
            onClick={() => setAutoRefresh((value) => !value)}
          >
            Auto-refresh: {autoRefresh ? "on (60s)" : "off"}
          </Button>
          {filtered && (
            <Button
              variant="outline"
              className="min-h-11"
              onClick={() =>
                setFilters({ ...initialFilters, days: filters.days })
              }
            >
              Clear filters
            </Button>
          )}
        </div>
      </div>
      {query.isPending && (
        <div
          role="status"
          className="rounded-xl border p-8 text-sm text-muted-foreground"
        >
          Loading AI usage history...
        </div>
      )}
      {query.isError && (
        <EmptyState
          variant="error"
          title={
            restricted
              ? "Administrator access required"
              : "Could not load AI usage"
          }
          description={
            restricted
              ? "Switch to an administrator role to view usage across users."
              : data
                ? "The last refresh failed. The dashboard below shows the previous snapshot."
                : "Usage history is temporarily unavailable. Retry to load the dashboard."
          }
          action={
            !restricted && (
              <Button
                variant="outline"
                className="min-h-11"
                onClick={() => void query.refetch()}
              >
                Retry
              </Button>
            )
          }
        />
      )}
      {data && !restricted && (
        <>
          {data.total === 0 && (
            <EmptyState
              title={
                data.partial
                  ? "No activity in the available records"
                  : "No AI activity in this selection"
              }
              description={
                filtered
                  ? "Clear the filters or choose a longer period."
                  : "Saved assistant turns, Smart runs, and audited AI SQL will appear here as you use Nova."
              }
            />
          )}
          <AIUsageDashboard data={data} setFilter={setFilter} />
        </>
      )}
    </div>
  );
}
