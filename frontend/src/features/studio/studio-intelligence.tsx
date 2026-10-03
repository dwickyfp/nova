import { useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { StatusBadge } from "@/components/ui/status-badge";
import { api, ApiError } from "@/lib/api-client";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuthStore } from "@/stores/auth-store";
import {
  intelligenceApi,
  type Decision,
  type Evidence,
  type Investigation,
  type News,
} from "@/features/intelligence/lifecycle-api";
import { DecisionCompose } from "@/features/intelligence/decision-compose";
import {
  DecisionShares,
  EffectivenessPanel,
} from "@/features/intelligence/decision-governance";
import { MonitorPanel } from "@/features/intelligence/monitor-panel";
import { ContextInspector } from "@/features/intelligence/context-inspector";

type Props = {
  view: "news" | "decisions";
  item?: string;
  onOpen: (id?: string) => void;
  onDecision: (id: string) => void;
  onFollowUp: (prompt: string) => void;
};
const number = (value: number | undefined | null) =>
  value == null
    ? "Unavailable"
    : new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(
        value,
      );

function Failure({ error, retry }: { error: unknown; retry: () => void }) {
  const status = error instanceof ApiError ? error.status : 0;
  return (
    <EmptyState
      variant="error"
      title={
        status === 409
          ? "Evidence needs revalidation"
          : status === 403 || status === 404
            ? "This item is unavailable with your current access"
            : "Unable to load intelligence"
      }
      description={
        error instanceof Error
          ? error.message
          : "Try again when the service is available."
      }
      action={
        <Button variant="outline" onClick={retry}>
          Retry
        </Button>
      }
    />
  );
}

export function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  return (
    <details className="rounded-md border p-3">
      <summary className="cursor-pointer text-sm font-medium focus-visible:outline focus-visible:outline-ring">
        Evidence ({evidence.length})
      </summary>
      <ul className="mt-3 space-y-3">
        {Array.from(
          new Map(evidence.map((item) => [item.id, item])).values(),
        ).map((item) => (
          <li key={item.id} className="break-words text-xs">
            <p className="font-medium">
              {item.source_type} · {item.method}
            </p>
            <p className="text-muted-foreground">
              Observed {new Date(item.observed_at).toLocaleString()}
              {item.semantic
                ? ` · Semantic version ${item.semantic.version}`
                : ""}
            </p>
            <p className="mt-1 break-all text-muted-foreground">
              Reference: {item.source_id}
            </p>
            <p className="break-all text-muted-foreground">
              Integrity: {item.digest}
            </p>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function StudioIntelligence(props: Props) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  return (
    <IntelligenceView
      key={`${epoch}:${props.view}:${props.item || ""}`}
      {...props}
      epoch={epoch}
    />
  );
}

function IntelligenceView({
  view,
  item,
  onOpen,
  onDecision,
  onFollowUp,
  epoch,
}: Props & { epoch: number }) {
  const client = useQueryClient();
  const [showMonitors, setShowMonitors] = useState(false);
  const [shared, setShared] = useState(false);
  const key = ["intelligence", epoch, view, shared ? "shared" : "owned"];
  const listing = useInfiniteQuery({
    queryKey: key,
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      intelligenceApi.page<News | Decision>(
        shared && view === "decisions" ? "decisions/shared" : view,
        pageParam,
      ),
    getNextPageParam: (page) => page.next_after || undefined,
    enabled: !item,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  const detail = useQuery({
    queryKey: [...key, item],
    queryFn: () => intelligenceApi.get<News | Decision>(view, item!),
    enabled: !!item,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["intelligence", epoch] });
  };
  const rows = listing.data?.pages.flatMap((page) => page.items) || [];
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b px-4 py-4 sm:px-6">
        {item && (
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Back to ${view}`}
            onClick={() => onOpen()}
          >
            <ArrowLeft className="size-4" />
          </Button>
        )}
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-normal">
            {view === "news" ? "News" : "Decisions"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {view === "news"
              ? "Material changes with evidence and investigation."
              : "Options, review, and observed outcomes."}
          </p>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={refresh}
          aria-label="Refresh intelligence"
        >
          <RefreshCw className="size-4" />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
        <div className="mx-auto max-w-4xl space-y-4">
          {view === "news" && !item && (
            <div className="space-y-4">
              <Button
                variant="outline"
                aria-expanded={showMonitors}
                onClick={() => setShowMonitors((value) => !value)}
              >
                {showMonitors ? "Close monitor settings" : "Manage monitors"}
              </Button>
              {showMonitors && <MonitorPanel onNews={onOpen} />}
            </div>
          )}
          {view === "decisions" && !item && (
            <div className="space-y-4">
              <div
                className="flex flex-wrap gap-2"
                aria-label="Decision visibility"
              >
                <Button
                  variant={shared ? "outline" : "secondary"}
                  aria-pressed={!shared}
                  onClick={() => setShared(false)}
                >
                  My decisions
                </Button>
                <Button
                  variant={shared ? "secondary" : "outline"}
                  aria-pressed={shared}
                  onClick={() => setShared(true)}
                >
                  Shared with me
                </Button>
              </div>
              {!shared && <EffectivenessPanel epoch={epoch} />}
            </div>
          )}
          {item ? (
            detail.isFetching ? (
              <LoadingLines />
            ) : detail.isError ? (
              <Failure
                error={detail.error}
                retry={() => void detail.refetch()}
              />
            ) : (
              detail.data &&
              (view === "news" ? (
                <NewsDetail
                  news={detail.data as News}
                  onCreated={onDecision}
                  onFollowUp={onFollowUp}
                  refresh={refresh}
                  epoch={epoch}
                />
              ) : (
                <DecisionDetail
                  decision={detail.data as Decision}
                  refresh={refresh}
                  epoch={epoch}
                />
              ))
            )
          ) : listing.isError ? (
            <Failure
              error={listing.error}
              retry={() => void listing.refetch()}
            />
          ) : listing.isFetching && !listing.isFetchingNextPage ? (
            <LoadingLines />
          ) : rows.length ? (
            <ul className="divide-y rounded-md border">
              {rows.map((row) => (
                <li key={row.id}>
                  <button
                    type="button"
                    className="flex w-full flex-col gap-2 p-4 text-left hover:bg-muted/50 focus-visible:outline focus-visible:outline-ring"
                    onClick={() => onOpen(row.id)}
                  >
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="min-w-0 flex-1 break-words font-medium">
                        {row.title}
                      </span>
                      <StatusBadge>
                        {row.status.split("_").join(" ")}
                      </StatusBadge>
                    </span>
                    {"summary" in row && (
                      <span className="text-sm text-muted-foreground">
                        {row.summary}
                      </span>
                    )}
                    <span className="text-xs text-muted-foreground">
                      {new Date(row.created_at).toLocaleString()} · Semantic
                      version {row.semantic.version}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title={
                view === "news"
                  ? "No material changes to review"
                  : "No decisions yet"
              }
              description={
                view === "news"
                  ? "News appears after an enabled monitor finds a change above its materiality threshold."
                  : "Open a News investigation to prepare options and an outcome window."
              }
            />
          )}
          {!item && listing.hasNextPage && (
            <Button
              variant="outline"
              disabled={listing.isFetchingNextPage}
              onClick={() => void listing.fetchNextPage()}
            >
              {listing.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function NewsDetail({
  news,
  onCreated,
  onFollowUp,
  refresh,
  epoch,
}: {
  news: News;
  onCreated: (id: string) => void;
  onFollowUp: Props["onFollowUp"];
  refresh: () => void;
  epoch: number;
}) {
  const [statusNote, setStatusNote] = useState("");
  const statusChange = useMutation({
    mutationFn: (operation: string) =>
      api.post(`/intelligence/news/${news.id}/operations`, {
        operation_id: crypto.randomUUID(),
        expected_revision: news.revision,
        operation,
        note: statusNote,
      }),
    onSuccess: refresh,
  });
  const [investigation, setInvestigation] = useState<Investigation | null>(
    null,
  );
  const stored = useQuery({
    queryKey: ["intelligence", epoch, "investigation", news.investigation_id],
    queryFn: () =>
      intelligenceApi.get<Investigation>(
        "investigations",
        news.investigation_id!,
      ),
    enabled: !!news.investigation_id,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  const investigate = useMutation({
    mutationFn: () => intelligenceApi.investigate(news.id),
    onSuccess: (value) => {
      setInvestigation(value);
      refresh();
    },
  });
  const value = investigation || stored.data;
  return (
    <article className="space-y-5">
      <div className="space-y-2">
        <div className="flex flex-wrap gap-2">
          <StatusBadge
            tone={news.severity === "critical" ? "danger" : "warning"}
          >
            {news.severity}
          </StatusBadge>
          <StatusBadge>{news.status}</StatusBadge>
        </div>
        <h2 className="break-words text-xl font-medium">{news.title}</h2>
        <p className="text-sm text-muted-foreground">{news.summary}</p>
      </div>
      <dl className="grid grid-cols-1 gap-3 rounded-md border p-4 sm:grid-cols-3">
        {[
          ["Baseline", news.before],
          ["Observed", news.after],
          ["Change", news.change],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="mt-1 text-lg tabular-nums">
              {number(value as number)}
            </dd>
          </div>
        ))}
      </dl>
      <p className="text-xs text-muted-foreground">
        {new Date(news.window.start).toLocaleString()} –{" "}
        {new Date(news.window.end).toLocaleString()} · Detection confidence:{" "}
        {news.confidence.label}
      </p>
      <details className="space-y-3 rounded-md border p-3">
        <summary className="cursor-pointer text-sm font-medium">
          Update incident status
        </summary>
        <Label htmlFor={`news-note-${news.id}`}>Reason</Label>
        <Input
          id={`news-note-${news.id}`}
          value={statusNote}
          onChange={(e) => setStatusNote(e.target.value)}
          maxLength={2000}
        />
        <div className="flex flex-wrap gap-2">
          {(news.status === "resolved" || news.status === "dismissed"
            ? ["reopen"]
            : ["resolve", "dismiss"]
          ).map((operation) => (
            <Button
              key={operation}
              size="sm"
              variant="outline"
              disabled={statusChange.isPending || !statusNote.trim()}
              onClick={() => statusChange.mutate(operation)}
            >
              {operation === "reopen"
                ? "Reopen"
                : operation === "resolve"
                  ? "Mark resolved"
                  : "Dismiss"}
            </Button>
          ))}
        </div>
        {statusChange.isError && (
          <Failure error={statusChange.error} retry={refresh} />
        )}
      </details>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={investigate.isPending}
          onClick={() => investigate.mutate()}
        >
          {investigate.isPending
            ? "Investigating…"
            : value
              ? "Refresh investigation"
              : "Investigate"}
        </Button>
        <Button
          variant="outline"
          onClick={() =>
            onFollowUp(
              `Investigate News ${news.id}: ${news.title}. Use its governed evidence and distinguish contribution, association, and causal evidence.`,
            )
          }
        >
          Continue in Studio
        </Button>
      </div>
      {investigate.isError && (
        <Failure error={investigate.error} retry={() => investigate.mutate()} />
      )}
      {stored.isFetching ? (
        <LoadingLines />
      ) : stored.isError ? (
        <Failure error={stored.error} retry={() => void stored.refetch()} />
      ) : (
        value && (
          <>
            <div className="space-y-3">
              <h3 className="font-medium">
                Ranked contributions and hypotheses
              </h3>
              {value.hypotheses.length ? (
                <ol className="space-y-3">
                  {value.hypotheses.map((hypothesis) => (
                    <li
                      key={hypothesis.id}
                      className="space-y-2 rounded-md border p-3"
                    >
                      <div className="flex flex-wrap justify-between gap-2">
                        <span className="break-words text-sm font-medium">
                          {hypothesis.label}
                        </span>
                        <StatusBadge>
                          {hypothesis.causal_status.split("_").join(" ")}
                        </StatusBadge>
                      </div>
                      {hypothesis.contribution != null && (
                        <p className="text-sm tabular-nums">
                          Contribution: {number(hypothesis.contribution)}
                        </p>
                      )}
                      <p className="text-xs text-muted-foreground">
                        {hypothesis.next_test}
                      </p>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="text-sm text-muted-foreground">
                  The available evidence does not identify a driver.
                </p>
              )}
              {value.decompositions.map((part) => (
                <p
                  key={part.dimension}
                  className="text-xs text-muted-foreground"
                >
                  {part.dimension}: residual {part.residual} ·{" "}
                  {part.reconciled ? "Reconciled" : "Incomplete reconciliation"}
                  . Each dimension is a separate comparison.
                </p>
              ))}
            </div>
            <div>
              <h3 className="mb-2 font-medium">Business and system timeline</h3>
              <ol className="space-y-2">
                {value.timeline.map((event, index) => (
                  <li
                    key={index}
                    className="border-l-2 border-border pl-3 text-sm"
                  >
                    <time className="text-xs text-muted-foreground">
                      {new Date(event.at).toLocaleString()}
                    </time>
                    <p>
                      {event.label || event.kind.split("_").join(" ")}
                      {event.before != null
                        ? ` · ${number(event.before)} → ${number(event.after)}`
                        : ""}
                    </p>
                    {event.causal_status && (
                      <p className="text-xs text-muted-foreground">
                        {event.causal_status}
                      </p>
                    )}
                  </li>
                ))}
              </ol>
            </div>
            <EvidenceList evidence={value.evidence} />
            <DecisionCompose
              news={news}
              investigation={value}
              onCreated={onCreated}
            />
          </>
        )
      )}
      {!value && <EvidenceList evidence={news.evidence} />}
    </article>
  );
}

function DecisionDetail({
  decision,
  refresh,
  epoch,
}: {
  decision: Decision;
  refresh: () => void;
  epoch: number;
}) {
  const client = useQueryClient();
  const policy = useQuery({
    queryKey: ["intelligence", epoch, "policy", decision.id, decision.revision],
    queryFn: () => intelligenceApi.policy(decision.id),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  const permissions =
    !policy.isFetching && !policy.isError ? policy.data : undefined;
  const operation = useMutation({
    mutationFn: ({ action, option }: { action: string; option?: string }) =>
      intelligenceApi.operate(decision, action, option),
    onSuccess: refresh,
  });
  const outcome = useMutation({
    mutationFn: () => intelligenceApi.outcome(decision.id),
    onSuccess: () =>
      client.invalidateQueries({
        queryKey: ["intelligence", epoch, "lineage", decision.id],
      }),
  });
  const context = useMutation({
    mutationFn: () =>
      api.post<{ root_id: string }>(
        `/intelligence/decisions/${encodeURIComponent(decision.id)}/context`,
      ),
  });
  const lineage = useQuery({
    queryKey: [
      "intelligence",
      epoch,
      "lineage",
      decision.id,
      decision.revision,
    ],
    queryFn: () => intelligenceApi.lineage(decision.id),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  const outcomeResult =
    !lineage.isFetching && !lineage.isError
      ? (outcome.data ?? lineage.data?.outcomes?.[0])
      : undefined;
  return (
    <article className="space-y-5">
      <div className="space-y-2">
        <h2 className="break-words text-xl font-medium">{decision.title}</h2>
        <StatusBadge>{decision.status.split("_").join(" ")}</StatusBadge>
        <p className="text-xs text-muted-foreground">
          Revision {decision.revision} · Outcome window{" "}
          {new Date(decision.outcome_window.start).toLocaleDateString()} –{" "}
          {new Date(decision.outcome_window.end).toLocaleDateString()}
        </p>
      </div>
      {decision.policy && (
        <div className="space-y-1 rounded-md border p-3">
          <StatusBadge
            tone={
              decision.policy.decision === "DENY"
                ? "danger"
                : decision.policy.decision === "ALLOW"
                  ? "success"
                  : "warning"
            }
          >
            {decision.policy.decision.split("_").join(" ")}
          </StatusBadge>
          <p className="text-sm">{decision.policy.reason}</p>
          <p className="text-xs text-muted-foreground">
            Policy revision {decision.policy.policy_revision}
          </p>
        </div>
      )}
      {permissions?.can_edit && (
        <DecisionShares decisionId={decision.id} epoch={epoch} />
      )}
      {policy.isError && (
        <Failure error={policy.error} retry={() => void policy.refetch()} />
      )}
      {permissions && decision.policy && !permissions.current && (
        <p role="status" className="text-sm text-destructive">
          Policy changed. The owner must select an option again before this
          decision can be approved.
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        Monetary estimates use {decision.currency}. Approval records a
        recommendation for review.
      </p>
      <h3 className="font-medium">Options and assumptions</h3>
      <div className="space-y-3">
        {decision.options.map((option) => (
          <section key={option.id} className="space-y-3 rounded-md border p-4">
            <div className="flex flex-wrap justify-between gap-2">
              <h4 className="font-medium">{option.description}</h4>
              <StatusBadge>
                {option.id === decision.selected_option_id
                  ? "Selected"
                  : option.feasible
                    ? `${option.risk} risk`
                    : "Infeasible"}
              </StatusBadge>
            </div>
            <dl className="grid gap-3 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">Predicted result</dt>
                <dd className="tabular-nums">{number(option.prediction)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Sensitivity range</dt>
                <dd className="tabular-nums">
                  {number(option.lower_bound)} – {number(option.upper_bound)}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">
                  Cost / incremental gross profit
                </dt>
                <dd className="tabular-nums">
                  {number(option.cost)} /{" "}
                  {number(option.incremental_gross_profit)}
                </dd>
              </div>
            </dl>
            <details>
              <summary className="cursor-pointer text-sm focus-visible:outline focus-visible:outline-ring">
                Assumptions
              </summary>
              <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
                {Object.entries(option.assumptions).map(([label, value]) => (
                  <div key={label}>
                    <dt className="text-muted-foreground">
                      {label.split("_").join(" ")}
                    </dt>
                    <dd className="break-words">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </details>
            <p className="text-xs text-muted-foreground">
              Method: {option.method}. Scenario demand changes are assumptions.
            </p>
            {permissions?.can_edit &&
              !["cancelled", "superseded", "evaluated"].includes(
                decision.status,
              ) && (
                <Button
                  variant="outline"
                  disabled={!option.feasible || operation.isPending}
                  onClick={() =>
                    operation.mutate({ action: "select", option: option.id })
                  }
                >
                  Select option
                </Button>
              )}
          </section>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        {decision.status === "awaiting_approval" &&
          permissions?.can_review &&
          permissions.current && (
            <>
              <Button
                disabled={operation.isPending}
                onClick={() => operation.mutate({ action: "approve" })}
              >
                Approve this revision
              </Button>
              <Button
                variant="outline"
                disabled={operation.isPending}
                onClick={() => operation.mutate({ action: "deny" })}
              >
                Deny
              </Button>
            </>
          )}
        {permissions?.can_edit &&
          ["approved", "selected", "observing", "evaluated"].includes(
            decision.status,
          ) && (
            <Button
              variant="outline"
              disabled={outcome.isPending}
              onClick={() => outcome.mutate()}
            >
              {outcome.isPending ? "Evaluating…" : "Evaluate outcome"}
            </Button>
          )}
        {permissions?.can_edit &&
          !["cancelled", "superseded", "evaluated"].includes(
            decision.status,
          ) && (
            <Button
              variant="outline"
              disabled={operation.isPending}
              onClick={() => operation.mutate({ action: "cancel" })}
            >
              Cancel decision
            </Button>
          )}
      </div>
      {operation.isError && <Failure error={operation.error} retry={refresh} />}
      {outcome.isError && (
        <Failure error={outcome.error} retry={() => outcome.mutate()} />
      )}
      {outcomeResult && (
        <section className="space-y-3 rounded-md border p-4">
          <h3 className="font-medium">
            Outcome: {outcomeResult.status.split("_").join(" ")}
          </h3>
          <p className="text-sm">
            Observed: {number(outcomeResult.actual)} · Completeness:{" "}
            {Math.round(outcomeResult.completeness * 100)}% · Attribution:{" "}
            {outcomeResult.attribution.split("_").join(" ")}
          </p>
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            {Object.entries(outcomeResult.dimensions).map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs text-muted-foreground">
                  {label.split("_").join(" ")}
                </dt>
                <dd>{value == null ? "Unavailable" : String(value)}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      <section className="space-y-3">
        <h3 className="font-medium">Lineage</h3>
        {permissions?.can_edit && (
          <Button
            variant="outline"
            disabled={context.isPending}
            onClick={() => context.mutate()}
          >
            {context.isPending
              ? "Preparing decision context…"
              : "Inspect decision context"}
          </Button>
        )}
        {context.isError && (
          <Failure error={context.error} retry={() => context.mutate()} />
        )}
        {context.data &&
          !context.isPending &&
          !context.isError &&
          !lineage.isFetching &&
          !lineage.isError && (
            <ContextInspector
              key={context.data.root_id}
              semantic={decision.semantic}
              initialNodeId={context.data.root_id}
            />
          )}
        {lineage.isFetching ? (
          <LoadingLines rows={2} />
        ) : lineage.isError ? (
          <Failure error={lineage.error} retry={() => void lineage.refetch()} />
        ) : (
          lineage.data && (
            <>
              <p className="text-sm">
                {lineage.data.news.title} → Investigation → Decision revision{" "}
                {decision.revision}
              </p>
              {lineage.data.events?.map((event) => (
                <p key={event.id} className="text-xs text-muted-foreground">
                  {event.event} · Revision {event.decision_revision} ·{" "}
                  {event.actor}
                </p>
              ))}
              <EvidenceList evidence={lineage.data.evidence} />
            </>
          )
        )}
      </section>
    </article>
  );
}
