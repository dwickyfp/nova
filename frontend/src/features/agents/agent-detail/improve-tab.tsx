import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { CircleAlert, CircleCheck, CircleX } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuthStore } from "@/stores/auth-store";
import { AgentQualityTab, QualitySuggestions } from "./quality-tab";
import { AgentHarnessTab } from "./harness-tab";
import { ImproveOverview, type ImproveSection } from "./improve-overview";
import { AgentMemoryDialog } from "@/features/studio/agent-memory-dialog";
import { api } from "@/lib/api-client";
import {
  learningApi,
  type ReadinessCheck,
  type VerifiedQueryCandidate,
} from "@/features/agents/studio-intelligence-api";

export function AgentImproveTab({
  agentId,
  onInspectTrace,
  onOpenConfiguration,
}: {
  agentId: string;
  onInspectTrace?: (traceId: string) => void;
  onOpenConfiguration?: () => void;
}) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  return (
    <ImproveWorkspace
      key={`${epoch}:${agentId}`}
      agentId={agentId}
      onInspectTrace={onInspectTrace}
      onOpenConfiguration={onOpenConfiguration}
    />
  );
}

function ImproveWorkspace({
  agentId,
  onInspectTrace,
  onOpenConfiguration,
}: {
  agentId: string;
  onInspectTrace?: (traceId: string) => void;
  onOpenConfiguration?: () => void;
}) {
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const consolidation = useMutation({
    mutationFn: () =>
      api.post<{ queued: number }>(
        `/agents/${encodeURIComponent(agentId)}/knowledge/consolidate`,
      ),
    onSuccess: (result) =>
      toast.success(`${result.queued} learning tasks queued`),
    onError: () =>
      toast.error(
        "Learning could not be queued. Check your current session and retry.",
      ),
  });
  const [section, setSection] = useState<ImproveSection>("overview");
  return (
    // The sub-tab row stays put; the open sub-tab's content scrolls below it.
    <Tabs
      value={section}
      onValueChange={(value) => setSection(value as ImproveSection)}
      className="flex min-h-0 min-w-0 flex-1 flex-col gap-0"
    >
      <TabsList
        aria-label="Improve agent"
        className="max-w-full shrink-0 self-start overflow-x-auto [scrollbar-width:none]"
        indicatorVariant="underline"
      >
        <TabsTrigger value="overview" className="min-h-11">
          Overview
        </TabsTrigger>
        <TabsTrigger value="quality" className="min-h-11">
          Tests
        </TabsTrigger>
        <TabsTrigger value="readiness" className="min-h-11">
          Data check
        </TabsTrigger>
        <TabsTrigger value="harness" className="min-h-11">
          Behavior rules
        </TabsTrigger>
        <TabsTrigger value="knowledge" className="min-h-11">
          Knowledge
        </TabsTrigger>
        <TabsTrigger value="suggestions" className="min-h-11">
          Suggestions
        </TabsTrigger>
      </TabsList>
      <div className="mt-6 min-h-0 flex-1 overflow-y-auto">
      <TabsContent value="overview" className="min-w-0">
        <ImproveOverview
          agentId={agentId}
          onOpen={setSection}
          onOpenConfiguration={onOpenConfiguration}
        />
      </TabsContent>
      <TabsContent value="quality" className="min-w-0 max-w-4xl space-y-8">
        <AgentQualityTab agentId={agentId} onInspectTrace={onInspectTrace} />
      </TabsContent>
      <TabsContent value="readiness" className="min-w-0 max-w-4xl space-y-8">
        <Intro>
          Checks on the business data this agent reads. Each fix helps it
          understand the words people use; make them in the Semantic View.
        </Intro>
        <Readiness agentId={agentId} />
      </TabsContent>
      <TabsContent value="harness" className="min-w-0 max-w-4xl space-y-8">
        <Intro>
          How the agent works: rules it always follows, tips for each tool, and
          safety rules that stop certain actions. Changes are saved as a draft
          you test and publish.
        </Intro>
        <AgentHarnessTab agentId={agentId} />
      </TabsContent>
      <TabsContent value="knowledge" className="min-w-0 max-w-4xl space-y-8">
        <Intro>
          What the agent has learned from your conversations, and answers people
          liked that you can approve so the same question gets the same answer.
        </Intro>
        <section className="space-y-3">
          <h2 className="text-base font-medium">What it remembers</h2>
          <p className="text-sm text-muted-foreground">
            Facts and business rules picked up from conversations. Review them,
            confirm the right ones, and remove the wrong ones.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => setKnowledgeOpen(true)}>
              Review knowledge
            </Button>
            <Button
              variant="outline"
              disabled={consolidation.isPending}
              onClick={() => consolidation.mutate()}
            >
              {consolidation.isPending
                ? "Queuing…"
                : "Learn from recent conversations"}
            </Button>
          </div>
        </section>
        <AgentMemoryDialog
          agentId={agentId}
          open={knowledgeOpen}
          onOpenChange={setKnowledgeOpen}
        />
        <Candidates agentId={agentId} />
      </TabsContent>
      <TabsContent value="suggestions" className="min-w-0 max-w-4xl space-y-8">
        <Intro>
          Fixes Nova proposes from failed tests, and signals from real use such
          as disliked answers. A fix you accept becomes a draft you test first.
        </Intro>
        <QualitySuggestions agentId={agentId} />
        <Suggestions agentId={agentId} />
      </TabsContent>
      </div>
    </Tabs>
  );
}

/** One plain sentence on what a sub-tab is for. */
function Intro({ children }: { children: React.ReactNode }) {
  return <p className="max-w-3xl text-sm text-muted-foreground">{children}</p>;
}

const STATUS_ICON = {
  ok: <CircleCheck className="size-4 text-success-strong" aria-hidden />,
  warn: <CircleAlert className="size-4 text-warning-strong" aria-hidden />,
  fail: <CircleX className="size-4 text-destructive" aria-hidden />,
} as const;

/** Each readiness check in plain words, by the stable part of its id. */
const CHECK_TITLES: Record<string, string> = {
  semantic_view: "It can read its business data",
  lint: "Problems in the data definition",
  dimensions: "Text fields can be used to group and filter",
  synonyms: "Everyday words for each number",
  values: "Example values for filters",
  time: "A date for each number",
  catalog: "It can see the whole definition at once",
  sample_questions: "Starter questions are set",
  tools: "It can query the data",
  charts: "It can draw charts",
  budget: "Room to break results down",
};

function checkTitle(check: ReadinessCheck): { title: string; view?: string } {
  const [kind, view] = check.id.split(":", 2);
  return { title: CHECK_TITLES[kind] ?? check.title, view };
}

function Readiness({ agentId }: { agentId: string }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const query = useQuery({
    queryKey: ["agents", "readiness", epoch, agentId],
    queryFn: () => learningApi.readiness(agentId),
  });
  const checks = query.data?.checks ?? [];
  const issues = checks.filter((check) => check.status !== "ok");
  const passing = checks.filter((check) => check.status === "ok");
  return (
    <section aria-labelledby="readiness-heading" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="readiness-heading" className="text-base font-medium">
          Data checks
        </h2>
        {query.data && (
          <span className="text-sm text-muted-foreground tabular-nums">
            {passing.length} of {checks.length} checks pass
          </span>
        )}
      </div>
      {query.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : query.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Readiness could not be checked.{" "}
          <Button variant="link" onClick={() => void query.refetch()}>
            Retry checks
          </Button>
        </p>
      ) : (
        <>
          {issues.length ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm">
                  {issues.length} thing{issues.length === 1 ? "" : "s"} to tidy
                  up, most useful first.
                </p>
                <Button asChild variant="outline" size="sm">
                  <a href="/semantic-views">Open Semantic Views</a>
                </Button>
              </div>
              <ul className="divide-y rounded-lg border">
                {[...issues]
                  .sort((a, b) => (a.status === "fail" ? -1 : 0) - (b.status === "fail" ? -1 : 0))
                  .map((check) => (
                    <ReadinessRow key={check.id} check={check} />
                  ))}
              </ul>
            </>
          ) : (
            <p className="text-sm">Everything checks out.</p>
          )}
          {passing.length ? (
            <details className="rounded-lg border">
              <summary className="cursor-pointer px-4 py-3 text-sm text-muted-foreground">
                {passing.length} check{passing.length === 1 ? "" : "s"} pass
              </summary>
              <ul className="divide-y border-t">
                {passing.map((check) => (
                  <ReadinessRow key={check.id} check={check} />
                ))}
              </ul>
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}

function ReadinessRow({ check }: { check: ReadinessCheck }) {
  const { title, view } = checkTitle(check);
  return (
    <li className="flex gap-3 px-4 py-3">
      <span className="mt-0.5 shrink-0">{STATUS_ICON[check.status]}</span>
      <div className="min-w-0 space-y-0.5">
        <p className="flex flex-wrap items-baseline gap-x-2 text-sm font-medium">
          {title}
          {view ? (
            <span className="font-mono text-xs font-normal text-muted-foreground">
              {view}
            </span>
          ) : null}
        </p>
        {check.detail && (
          <p className="text-sm text-muted-foreground break-words">
            {check.detail}
          </p>
        )}
        {check.fix && <p className="text-sm">{check.fix}</p>}
      </div>
    </li>
  );
}

function Candidates({ agentId }: { agentId: string }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["agents", "verified-candidates", epoch, agentId],
    queryFn: () => learningApi.candidates(agentId),
  });
  const decide = useMutation({
    mutationFn: ({
      id,
      decision,
    }: {
      id: string;
      decision: "approve" | "reject";
    }) => learningApi.decide(agentId, id, decision),
    onSuccess: (result) => {
      toast.success(
        result.status === "approved"
          ? `Added to draft version ${result.draft_version ?? ""}. Publish it from the Semantic View.`
          : "Candidate dismissed",
      );
      queryClient.invalidateQueries({
        queryKey: ["agents", "verified-candidates", epoch, agentId],
      });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const candidates = query.data?.candidates ?? [];
  return (
    <section aria-labelledby="candidates-heading" className="space-y-3">
      <div>
        <h2 id="candidates-heading" className="text-base font-medium">
          Liked answers to approve
        </h2>
        <p className="text-sm text-muted-foreground">
          Answers someone gave a thumbs up. Approve one and the same question
          is answered the same way next time.
        </p>
      </div>
      {query.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : query.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Liked answers could not be loaded.{" "}
          <Button variant="link" onClick={() => void query.refetch()}>
            Retry candidates
          </Button>
        </p>
      ) : candidates.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No liked answers are waiting for review.
        </p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {candidates.map((candidate) => (
            <CandidateRow
              key={candidate.candidate_id}
              candidate={candidate}
              busy={decide.isPending}
              onDecide={(decision) =>
                decide.mutate({ id: candidate.candidate_id, decision })
              }
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function CandidateRow({
  candidate,
  busy,
  onDecide,
}: {
  candidate: VerifiedQueryCandidate;
  busy: boolean;
  onDecide: (decision: "approve" | "reject") => void;
}) {
  const plan = candidate.semantic_plan as {
    metrics?: string[];
    dimensions?: string[];
  };
  return (
    <li className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 space-y-1">
        <p className="text-sm break-words">{candidate.question}</p>
        <p className="text-xs text-muted-foreground break-words">
          {(plan.metrics ?? []).join(", ")}
          {plan.dimensions?.length ? ` by ${plan.dimensions.join(", ")}` : ""} ·
          liked by {candidate.proposed_by}
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => onDecide("reject")}
        >
          Dismiss
        </Button>
        <Button size="sm" disabled={busy} onClick={() => onDecide("approve")}>
          Approve
        </Button>
      </div>
    </li>
  );
}

function Suggestions({ agentId }: { agentId: string }) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const query = useQuery({
    queryKey: ["agents", "improvement-suggestions", epoch, agentId],
    queryFn: () => learningApi.suggestions(agentId),
  });
  const feedback = query.data?.feedback ?? [];
  const views = query.data?.materialized_views ?? [];
  return (
    <section aria-labelledby="suggestions-heading" className="space-y-3">
      <h2 id="suggestions-heading" className="text-base font-medium">
        Signals from real use
      </h2>
      {query.isLoading ? (
        <Skeleton className="h-20 w-full" />
      ) : query.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Usage suggestions could not be loaded.{" "}
          <Button variant="link" onClick={() => void query.refetch()}>
            Retry usage suggestions
          </Button>
        </p>
      ) : feedback.length === 0 && views.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing yet. Disliked answers and questions asked often appear here.
        </p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {feedback.map((item) => (
            <li
              key={`${item.semantic_model_id}-${item.kind}`}
              className="space-y-1 px-4 py-3"
            >
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
                {item.dimensions.length
                  ? ` by ${item.dimensions.join(", ")}`
                  : ""}
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
