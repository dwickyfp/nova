import { useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleDashed, CircleX, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  deepResearchApi,
  type DeepResearchRun,
} from "@/features/agents/studio-intelligence-api";

const TERMINAL = new Set(["done", "failed", "cancelled", "interrupted"]);

const STATUS_TEXT: Record<DeepResearchRun["status"], string> = {
  planning: "Planning the investigations",
  running: "Running investigations",
  writing: "Writing the report",
  done: "Report ready",
  failed: "The research failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted before it finished",
};

/**
 * Progress of one Deep Research run: the plan, each investigation's state, and
 * a way into the report thread when it is done.
 */
export function DeepResearchCard({
  agentId,
  runId,
  onOpenReport,
  onDismiss,
}: {
  agentId: string;
  runId: string;
  onOpenReport: (threadId: string) => void;
  onDismiss: () => void;
}) {
  const query = useQuery({
    queryKey: ["studio", "deep-research", runId],
    queryFn: () => deepResearchApi.get(agentId, runId),
    refetchInterval: (state) =>
      state.state.data && TERMINAL.has(state.state.data.status) ? false : 3000,
  });
  const run = query.data;
  const finished = run ? TERMINAL.has(run.status) : false;
  return (
    <section
      aria-label="Deep research"
      aria-live="polite"
      className="nova-chat-item mb-3 rounded-xl border bg-card px-4 py-3"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium">Deep research</p>
          <p className="text-sm text-muted-foreground break-words">{run?.question ?? "Starting"}</p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {!finished && (
            <Button size="sm" variant="ghost" onClick={() => void deepResearchApi.cancel(agentId, runId)}>
              Cancel
            </Button>
          )}
          <Button size="icon" variant="ghost" aria-label="Dismiss research progress" onClick={onDismiss}>
            <X className="size-4" />
          </Button>
        </div>
      </div>
      <p className="mt-2 flex items-center gap-2 text-sm">
        {!finished && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
        {run ? STATUS_TEXT[run.status] : "Starting"}
      </p>
      {run?.progress?.length ? (
        <ol className="mt-2 space-y-1">
          {run.progress.map((item, index) => (
            <li key={index} className="flex items-start gap-2 text-sm">
              <span className="mt-0.5 shrink-0">
                {item.status === "done" ? (
                  <CircleCheck aria-label="Done" className="size-3.5 text-success-strong" />
                ) : item.status === "running" ? (
                  <Loader2 aria-label="Running" className="size-3.5 animate-spin" />
                ) : ["failed", "timeout", "cancelled"].includes(item.status) ? (
                  <CircleX aria-label={item.status} className="size-3.5 text-destructive" />
                ) : (
                  <CircleDashed aria-label="Waiting" className="size-3.5 text-muted-foreground" />
                )}
              </span>
              <span className="min-w-0 break-words text-muted-foreground">{item.question}</span>
            </li>
          ))}
        </ol>
      ) : null}
      {run?.status === "done" && (
        <Button className="mt-3" size="sm" onClick={() => onOpenReport(run.thread_id)}>
          Open the report
        </Button>
      )}
    </section>
  );
}
