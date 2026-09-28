import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleX } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  learningApi,
  type ReadinessCheck,
  type VerifiedQueryCandidate,
} from "@/features/agents/studio-intelligence-api";

/**
 * Improve: what to fix before users rely on this agent, and what users taught it.
 *
 * Readiness reads configuration and semantic metadata only. Candidates come from
 * answers users liked; approving one adds a verified query to a new draft
 * version of the Semantic View, which is published from the view's own page.
 */
export function AgentImproveTab({ agentId }: { agentId: string }) {
  return (
    <div className="grid max-w-4xl gap-8">
      <Readiness agentId={agentId} />
      <Candidates agentId={agentId} />
      <Suggestions agentId={agentId} />
    </div>
  );
}

const STATUS_ICON = {
  ok: <CircleCheck className="size-4 text-success-strong" aria-hidden />,
  warn: <CircleAlert className="size-4 text-warning-strong" aria-hidden />,
  fail: <CircleX className="size-4 text-destructive" aria-hidden />,
} as const;

function Readiness({ agentId }: { agentId: string }) {
  const query = useQuery({
    queryKey: ["agents", "readiness", agentId],
    queryFn: () => learningApi.readiness(agentId),
  });
  return (
    <section aria-labelledby="readiness-heading" className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="readiness-heading" className="text-base font-medium">Readiness</h2>
        {query.data && (
          <span className="text-sm text-muted-foreground">
            {query.data.checks.filter((check) => check.status === "ok").length} of{" "}
            {query.data.checks.length} checks pass
          </span>
        )}
      </div>
      {query.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : query.isError ? (
        <p role="alert" className="text-sm text-destructive">Readiness could not be checked.</p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {query.data?.checks.map((check) => <ReadinessRow key={check.id} check={check} />)}
        </ul>
      )}
    </section>
  );
}

function ReadinessRow({ check }: { check: ReadinessCheck }) {
  return (
    <li className="flex gap-3 px-4 py-3">
      <span className="mt-0.5 shrink-0">{STATUS_ICON[check.status]}</span>
      <div className="min-w-0 space-y-0.5">
        <p className="text-sm font-medium">{check.title}</p>
        {check.detail && <p className="text-sm text-muted-foreground break-words">{check.detail}</p>}
        {check.fix && <p className="text-sm">{check.fix}</p>}
      </div>
    </li>
  );
}

function Candidates({ agentId }: { agentId: string }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["agents", "verified-candidates", agentId],
    queryFn: () => learningApi.candidates(agentId),
  });
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) =>
      learningApi.decide(agentId, id, decision),
    onSuccess: (result) => {
      toast.success(
        result.status === "approved"
          ? `Added to draft version ${result.draft_version ?? ""}. Publish it from the Semantic View.`
          : "Candidate dismissed",
      );
      queryClient.invalidateQueries({ queryKey: ["agents", "verified-candidates", agentId] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const candidates = query.data?.candidates ?? [];
  return (
    <section aria-labelledby="candidates-heading" className="space-y-3">
      <div>
        <h2 id="candidates-heading" className="text-base font-medium">Liked answers to verify</h2>
        <p className="text-sm text-muted-foreground">
          Approving adds the question and its plan as a verified query, so the same
          question is answered the same way next time.
        </p>
      </div>
      {query.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : candidates.length === 0 ? (
        <p className="text-sm text-muted-foreground">No liked answers are waiting for review.</p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {candidates.map((candidate) => (
            <CandidateRow
              key={candidate.candidate_id}
              candidate={candidate}
              busy={decide.isPending}
              onDecide={(decision) => decide.mutate({ id: candidate.candidate_id, decision })}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function CandidateRow({
  candidate, busy, onDecide,
}: {
  candidate: VerifiedQueryCandidate;
  busy: boolean;
  onDecide: (decision: "approve" | "reject") => void;
}) {
  const plan = candidate.semantic_plan as { metrics?: string[]; dimensions?: string[] };
  return (
    <li className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 space-y-1">
        <p className="text-sm break-words">{candidate.question}</p>
        <p className="text-xs text-muted-foreground break-words">
          {(plan.metrics ?? []).join(", ")}
          {plan.dimensions?.length ? ` by ${plan.dimensions.join(", ")}` : ""} · liked by{" "}
          {candidate.proposed_by}
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
        <Button size="sm" variant="outline" disabled={busy} onClick={() => onDecide("reject")}>
          Dismiss
        </Button>
        <Button size="sm" disabled={busy} onClick={() => onDecide("approve")}>
          Verify
        </Button>
      </div>
    </li>
  );
}

function Suggestions({ agentId }: { agentId: string }) {
  const query = useQuery({
    queryKey: ["agents", "improvement-suggestions", agentId],
    queryFn: () => learningApi.suggestions(agentId),
  });
  const feedback = query.data?.feedback ?? [];
  const views = query.data?.materialized_views ?? [];
  return (
    <section aria-labelledby="suggestions-heading" className="space-y-3">
      <h2 id="suggestions-heading" className="text-base font-medium">From usage</h2>
      {query.isLoading ? (
        <Skeleton className="h-20 w-full" />
      ) : feedback.length === 0 && views.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing yet. Disliked answers and frequent query shapes appear here.
        </p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {feedback.map((item) => (
            <li key={`${item.semantic_model_id}-${item.kind}`} className="space-y-1 px-4 py-3">
              <p className="text-sm font-medium">
                {item.count} disliked answer{item.count === 1 ? "" : "s"}
              </p>
              <p className="text-sm text-muted-foreground break-words">
                For example: {item.examples.slice(0, 3).join(" · ")}
              </p>
            </li>
          ))}
          {views.map((item) => (
            <li
              key={`${item.metrics.join()}-${item.dimensions.join()}-${item.time_grain}`}
              className="flex flex-wrap items-center gap-2 px-4 py-3"
            >
              <Badge variant="outline">{item.query_count} queries</Badge>
              <span className="text-sm break-words">
                {item.metrics.join(", ")}
                {item.dimensions.length ? ` by ${item.dimensions.join(", ")}` : ""}
                {item.time_grain ? ` per ${item.time_grain}` : ""}
              </span>
              <span className="text-sm text-muted-foreground">
                is asked often; a materialized view would make it faster.
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
