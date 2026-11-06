import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { StatusBadge, type StatusTone } from "@/components/ui/status-badge";
import { Textarea } from "@/components/ui/textarea";
import {
  newspaperApi,
  type CausalStatus,
  type Incident,
  type IncidentStatus,
  type InvestigationUpdate,
} from "./newspaper-api";

const STATUS: Record<IncidentStatus, { label: string; tone: StatusTone }> = {
  OPEN: { label: "New", tone: "warning" },
  TRIAGED: { label: "Triaged", tone: "info" },
  INVESTIGATING: { label: "Investigating", tone: "info" },
  DECISION_PENDING: { label: "Decision pending", tone: "info" },
  ACTION_IN_PROGRESS: { label: "Action in progress", tone: "info" },
  OBSERVING: { label: "Observing the outcome", tone: "info" },
  RESOLVED: { label: "Resolved", tone: "success" },
  DISMISSED: { label: "Dismissed", tone: "neutral" },
};

const CAUSAL: Record<CausalStatus, string> = {
  arithmetic: "Arithmetic breakdown",
  association: "Moves together; not a cause",
  unknown: "Not established",
};

const GROUPING = {
  opened: "",
  same_concept: "same business concept",
  related_concepts: "linked business concepts",
  same_metric: "same metric",
} as const;

function signalName(signal: Incident["signals"][number]) {
  return signal.concept?.qualified_name ?? signal.metric;
}

function UpdateReview({
  incidentId,
  update,
  queryKey,
}: {
  incidentId: string;
  update: InvestigationUpdate;
  queryKey: unknown[];
}) {
  const client = useQueryClient();
  const [decision, setDecision] = useState<"APPROVE" | "REJECT" | null>(null);
  const [rationale, setRationale] = useState("");
  const [operationId] = useState(() => crypto.randomUUID());
  const review = useMutation({
    mutationFn: () =>
      newspaperApi.reviewUpdate(incidentId, update.id, {
        decision: decision!,
        rationale: rationale.trim(),
        operation_id: operationId,
      }),
    onSuccess: (incident) => {
      client.setQueryData(queryKey, { incident });
      toast.success(decision === "APPROVE" ? "Finding shown in News" : "Finding rejected");
    },
    onError: (error: Error) => toast.error(error.message),
  });
  if (decision === null) {
    return (
      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="sm" onClick={() => setDecision("APPROVE")}>Approve finding</Button>
        <Button size="sm" variant="outline" onClick={() => setDecision("REJECT")}>
          Reject
        </Button>
      </div>
    );
  }
  const id = `review-${update.id}`;
  return (
    <div className="mt-2 grid gap-2">
      <Label htmlFor={id}>
        {decision === "APPROVE" ? "What you checked" : "Why it is rejected"}
      </Label>
      <Textarea id={id} rows={2} value={rationale} onChange={(event) => setRationale(event.target.value)} />
      <div className="flex gap-2">
        <Button
          size="sm"
          variant={decision === "APPROVE" ? "default" : "destructive"}
          disabled={!rationale.trim() || review.isPending}
          onClick={() => review.mutate()}
        >
          {decision === "APPROVE" ? "Approve" : "Reject"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setDecision(null)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** The incident a story belongs to, as this reader may see it. */
export function IncidentSection({ storyId, epoch }: { storyId: string; epoch: number }) {
  const queryKey = ["newspaper", epoch, "story-incident", storyId];
  const incident = useQuery({
    queryKey,
    queryFn: ({ signal }) => newspaperApi.incident(storyId, signal),
    retry: false,
  });
  if (incident.isLoading) return <LoadingLines rows={2} />;
  if (incident.isError) {
    return <p className="text-muted-foreground">The incident could not be loaded.</p>;
  }
  const data = incident.data?.incident;
  if (!data) {
    return (
      <p className="text-muted-foreground">
        No related signals yet. Investigating opens a Studio Mission for this story.
      </p>
    );
  }
  const others = data.signals.filter((signal) => signal.source.id !== storyId);
  const byId = new Map(data.signals.map((signal) => [signal.id, signal]));
  const status = STATUS[data.status];
  const approved = data.updates.filter((update) => update.status === "APPROVED");
  const pending = data.updates.filter((update) => update.status === "PENDING_REVIEW");
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
        <span className="text-muted-foreground">
          {others.length === 0
            ? "No other signal you can see moved with this one."
            : `${others.length} related signal${others.length === 1 ? "" : "s"} you can see`}
        </span>
      </div>
      {others.length > 0 && (
        <ul className="grid gap-1">
          {others.map((signal) => {
            const reason = data.grouping.find((item) => item.signal_id === signal.id);
            return (
              <li key={signal.id}>
                <span className="font-medium">{signalName(signal)}</span>{" "}
                <span className="text-muted-foreground">
                  {signal.direction === "increase" ? "rose" : "fell"}
                  {reason && reason.rule !== "opened" ? `, ${GROUPING[reason.rule]}` : ""}
                  {reason?.with_signal_id && byId.get(reason.with_signal_id)
                    ? ` as ${signalName(byId.get(reason.with_signal_id)!)}`
                    : ""}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">{data.note}</p>
      {approved.length > 0 && (
        <div className="grid gap-3">
          <h4 className="text-sm font-medium">Investigated</h4>
          {approved.map((update) => (
            <article key={update.id} className="rounded-md border p-3">
              <p>{update.summary}</p>
              <ul className="mt-2 grid gap-1 text-sm">
                {update.claims.map((claim, index) => (
                  <li key={index}>
                    {claim.statement}{" "}
                    <span className="text-xs text-muted-foreground">
                      ({CAUSAL[claim.causal_status]})
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-muted-foreground">
                Found by {update.author}, reviewed by {update.review?.principal}
              </p>
            </article>
          ))}
        </div>
      )}
      {pending.length > 0 && (
        <div className="grid gap-3">
          <h4 className="text-sm font-medium">Waiting for review</h4>
          {pending.map((update) => (
            <article key={update.id} className="rounded-md border border-dashed p-3">
              <p>{update.summary}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Posted by {update.author}. Not shown as a finding until another reader in the
                same role approves it.
              </p>
              {update.can_review && (
                <UpdateReview incidentId={data.id} update={update} queryKey={queryKey} />
              )}
            </article>
          ))}
        </div>
      )}
      {data.transitions.length > 0 && (
        <ol className="grid gap-1 text-xs text-muted-foreground">
          {data.transitions.map((item, index) => (
            <li key={index}>
              {STATUS[item.to_status].label} by {item.principal}: {item.reason}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
