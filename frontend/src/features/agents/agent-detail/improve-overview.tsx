import { useQuery } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/auth-store";
import { agentVersionsApi } from "../api";
import { qualityApi } from "../quality-api";
import { learningApi } from "../studio-intelligence-api";

export type ImproveSection =
  | "overview"
  | "quality"
  | "readiness"
  | "harness"
  | "knowledge"
  | "suggestions";

type Step = {
  title: string;
  why: string;
  status: string;
  done: boolean;
  action: string;
  section: ImproveSection;
  loading: boolean;
  /** Overrides opening a sub-tab, e.g. to save the configuration first. */
  onAction?: () => void;
};

/**
 * Improve, explained: four steps in the order they pay off, each with where
 * this agent stands now and the one thing to do next. It reads the same data
 * as the detailed sub-tabs (same query keys), so opening one is instant.
 */
export function ImproveOverview({
  agentId,
  onOpen,
  onOpenConfiguration,
}: {
  agentId: string;
  onOpen: (section: ImproveSection) => void;
  /** Opens the agent's Configuration, where a version is saved. */
  onOpenConfiguration?: () => void;
}) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const quality = ["agent-quality", epoch, agentId];
  const readiness = useQuery({
    queryKey: ["agents", "readiness", epoch, agentId],
    queryFn: () => learningApi.readiness(agentId),
  });
  const cases = useQuery({
    queryKey: [...quality, "cases"],
    queryFn: ({ signal }) => qualityApi.cases(agentId, signal),
    retry: false,
  });
  const versions = useQuery({
    queryKey: [...quality, "versions"],
    queryFn: () => agentVersionsApi.list(agentId),
    retry: false,
  });
  const runs = useQuery({
    queryKey: [...quality, "runs"],
    queryFn: ({ signal }) => qualityApi.runs(agentId, signal),
    retry: false,
  });
  const liked = useQuery({
    queryKey: ["agents", "verified-candidates", epoch, agentId],
    queryFn: () => learningApi.candidates(agentId),
  });
  const proposals = useQuery({
    queryKey: [...quality, "proposals"],
    queryFn: ({ signal }) => qualityApi.proposals(agentId, signal),
    retry: false,
  });
  const usage = useQuery({
    queryKey: ["agents", "improvement-suggestions", epoch, agentId],
    queryFn: () => learningApi.suggestions(agentId),
  });

  const checks = readiness.data?.checks ?? [];
  const passing = checks.filter((check) => check.status === "ok").length;
  const toFix = checks.length - passing;

  const caseCount = cases.data?.items?.length ?? 0;
  const mustPass = (cases.data?.items ?? []).filter(
    (item) => item.mandatory && item.critical,
  ).length;

  const saved = (versions.data?.versions?.length ?? 0) > 0;
  const latest = [...(runs.data?.items ?? [])].sort((a, b) =>
    b.created_at.localeCompare(a.created_at),
  )[0];

  const likedCount = liked.data?.count ?? liked.data?.candidates?.length ?? 0;
  const pendingFixes = (proposals.data?.items ?? []).filter((item) =>
    ["pending", "proposed"].includes(item.status),
  ).length;
  const disliked = (usage.data?.feedback ?? []).reduce(
    (sum, item) => sum + item.count,
    0,
  );
  const toReview = likedCount + pendingFixes;

  const steps: Step[] = [
    {
      title: "Check the data it reads",
      why: "The agent answers from your business data. It understands you better when that data has clear names, the words people say (like “omzet”), and example values (like “Jakarta”).",
      status: !checks.length
        ? "Not checked yet"
        : toFix === 0
          ? `All ${checks.length} checks pass`
          : `${passing} of ${checks.length} checks pass · ${toFix} to tidy up`,
      done: checks.length > 0 && toFix === 0,
      action: toFix ? "See what to fix" : "View checks",
      section: "readiness",
      loading: readiness.isPending,
    },
    {
      title: "Write test questions",
      why: "Save the questions people really ask, with what a good answer must do. They tell you whether a change made the agent better or worse.",
      status: cases.isError
        ? "Testing is unavailable here"
        : caseCount === 0
          ? "No test questions yet"
          : `${caseCount} test question${caseCount === 1 ? "" : "s"} · ${mustPass} must pass before publishing`,
      done: mustPass > 0,
      action: caseCount ? "Manage test questions" : "Add a test question",
      section: "quality",
      loading: cases.isPending,
    },
    {
      title: "Test before you publish",
      why: "Run every test question on a saved version. A version that fails a must-pass test cannot be published, so a change never makes things worse unnoticed.",
      status: !saved
        ? "Save the configuration first, then test it"
        : !latest
          ? "Not tested yet"
          : latest.status === "running"
            ? "A test run is in progress"
            : `Last run ${latest.status === "passed" ? "passed" : latest.status === "failed" ? "failed" : "did not finish"} · ${new Date(latest.created_at).toLocaleDateString(undefined, { dateStyle: "medium" })}`,
      done: latest?.status === "passed",
      action: !saved && onOpenConfiguration ? "Open Configuration" : latest ? "See results" : "Run tests",
      section: "quality",
      onAction: !saved ? onOpenConfiguration : undefined,
      loading: versions.isPending || runs.isPending,
    },
    {
      title: "Learn from real use",
      why: "Answers people liked can become approved answers, and failed tests or disliked answers become suggested fixes you can review.",
      status:
        toReview || disliked
          ? [
              likedCount ? `${likedCount} liked answer${likedCount === 1 ? "" : "s"} to approve` : "",
              pendingFixes ? `${pendingFixes} suggested fix${pendingFixes === 1 ? "" : "es"}` : "",
              disliked ? `${disliked} disliked answer${disliked === 1 ? "" : "s"}` : "",
            ]
              .filter(Boolean)
              .join(" · ")
          : "Nothing waiting for review",
      done: toReview === 0,
      action: likedCount && !pendingFixes ? "Review liked answers" : "Review suggestions",
      section: likedCount && !pendingFixes ? "knowledge" : "suggestions",
      loading: liked.isPending || proposals.isPending || usage.isPending,
    },
  ];
  const next = steps.findIndex((step) => !step.loading && !step.done);

  return (
    <section aria-labelledby="improve-overview-heading" className="max-w-3xl space-y-6">
      <div className="space-y-1">
        <h2 id="improve-overview-heading" className="text-base font-medium">
          Make this agent better
        </h2>
        <p className="text-sm text-muted-foreground">
          Four steps, in the order they pay off. Each shows where this agent
          stands now and what to do next.
        </p>
      </div>
      <ol className="space-y-3">
        {steps.map((step, index) => {
          const isNext = index === next;
          return (
            <li
              key={step.title}
              aria-current={isNext ? "step" : undefined}
              className={cn(
                "flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center sm:gap-4",
                isNext && "border-primary/50 bg-muted/30",
              )}
            >
              <div className="flex min-w-0 flex-1 gap-4">
              <span
                aria-hidden="true"
                className={cn(
                  "grid size-7 shrink-0 place-items-center rounded-full border text-sm tabular-nums",
                  step.done
                    ? "border-success/40 bg-success/10 text-success-strong"
                    : isNext
                      ? "border-primary/60 text-foreground"
                      : "text-muted-foreground",
                )}
              >
                {step.done ? <Check className="size-4" /> : index + 1}
              </span>
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <h3 className="text-sm font-medium">{step.title}</h3>
                  {isNext ? (
                    <span className="text-xs text-primary">Next step</span>
                  ) : null}
                </div>
                <p className="text-sm text-muted-foreground">{step.why}</p>
                {step.loading ? (
                  <Skeleton className="h-5 w-48" />
                ) : (
                  <p className="text-sm tabular-nums">{step.status}</p>
                )}
              </div>
              </div>
              <Button
                variant={isNext ? "default" : "outline"}
                size="sm"
                className="ml-11 shrink-0 self-start sm:ml-0 sm:self-center"
                onClick={() => (step.onAction ? step.onAction() : onOpen(step.section))}
              >
                {step.action}
              </Button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
