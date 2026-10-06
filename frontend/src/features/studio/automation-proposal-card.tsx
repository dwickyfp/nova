import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { AUTO_AGENT_ID } from "@/features/agents/api";
import { automationsApi } from "@/features/agents/studio-intelligence-api";
import type { AutomationProposal } from "@/features/assistant/types";

const CRON_WORDS: Record<string, string> = {
  "0 8 * * *": "every day at 08:00",
  "0 8 * * 1": "every Monday at 08:00",
  "0 8 1 * *": "on the 1st of each month at 08:00",
};

/** The schedule as a reader says it; an unusual expression is shown as written. */
function describeSchedule(proposal: AutomationProposal): string {
  return CRON_WORDS[proposal.schedule_expr.trim()]
    ?? (proposal.schedule_kind === "cron"
      ? `on the schedule ${proposal.schedule_expr}`
      : proposal.schedule_expr.toLowerCase());
}

function words(identifier: string): string {
  return identifier.replace(/_/g, " ");
}

/**
 * A recurring report or alert Smart drafted. Smart cannot create it: a schedule
 * is the user's decision, so it starts only when they press Schedule here.
 */
export function AutomationProposalCard({ proposal }: { proposal: AutomationProposal }) {
  const queryClient = useQueryClient();
  const [dismissed, setDismissed] = useState(false);
  const existing = useQuery({
    queryKey: ["agents", "automations", AUTO_AGENT_ID],
    queryFn: () => automationsApi.list(AUTO_AGENT_ID),
  });
  const scheduled = existing.data?.automations.some((item) =>
    item.title === proposal.title && item.prompt === proposal.prompt
    && item.schedule_expr === proposal.schedule_expr);
  const create = useMutation({
    mutationFn: () => automationsApi.create(AUTO_AGENT_ID, {
      title: proposal.title,
      prompt: proposal.prompt,
      schedule_kind: proposal.schedule_kind,
      schedule_expr: proposal.schedule_expr,
      timezone: proposal.timezone,
      ...(proposal.condition
        ? { condition: proposal.condition as NonNullable<Parameters<typeof automationsApi.create>[1]["condition"]> }
        : {}),
    }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agents", "automations", AUTO_AGENT_ID] });
      toast.success(`"${proposal.title}" is scheduled`);
    },
    onError: () => toast.error("The schedule could not be created. Try again."),
  });
  if (dismissed && !scheduled) return null;
  const nextRun = proposal.next_run ? new Date(proposal.next_run) : null;
  return (
    <section
      aria-label="Proposed schedule"
      className="nova-chat-item rounded-xl border border-border bg-card px-4 py-3 text-sm text-card-foreground"
    >
      <div className="flex items-start gap-3">
        <CalendarClock aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
        <div className="min-w-0 flex-1 space-y-1">
          <p className="font-medium break-words">{proposal.title}</p>
          <p className="text-muted-foreground">
            Runs {describeSchedule(proposal)} ({proposal.timezone}) and asks:{" "}
            <span className="text-foreground break-words">{proposal.prompt}</span>
          </p>
          {proposal.condition ? (
            <p className="text-muted-foreground">
              Sends a result only when {words(proposal.condition.metric)} is{" "}
              {proposal.condition.operator} {proposal.condition.value.toLocaleString()}.
            </p>
          ) : null}
          <p className="text-muted-foreground">
            {scheduled
              ? "Scheduled. Each run reads with your access and appears in history. Manage it under Capabilities, Scheduled reports."
              : nextRun && !Number.isNaN(nextRun.getTime())
                ? `Nothing is scheduled yet. The first run would be ${nextRun.toLocaleString()}.`
                : "Nothing is scheduled yet."}
          </p>
        </div>
      </div>
      {scheduled ? null : (
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={() => setDismissed(true)} disabled={create.isPending}>
            Not now
          </Button>
          <Button type="button" size="sm" onClick={() => create.mutate()} disabled={create.isPending || existing.isLoading}>
            {create.isPending ? "Scheduling" : "Schedule"}
          </Button>
        </div>
      )}
    </section>
  );
}
