import { useId, useState } from "react";
import { Ban, Check, ChevronDown, Clock, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { HitlPart } from "../turn-reducer";
import { hitlKind } from "./registry";
import type { HitlAnswerBody, HitlRequestSpec, HitlStatus } from "./types";

const DEFAULT_SUMMARY: Record<Exclude<HitlStatus, "pending">, string> = {
  answered: "You answered",
  declined: "You skipped this question",
  allowed: "You allowed this action",
  denied: "You denied this action",
  edited: "You edited the request and ran it",
  refused: "Nova refused the edited request",
  expired: "The request expired",
  cancelled: "Stopped before you answered",
};

function statusIcon(status: HitlStatus) {
  if (status === "denied" || status === "refused") return X;
  if (status === "expired") return Clock;
  if (status === "cancelled" || status === "declined") return Ban;
  return Check;
}

function expiryText(expiresAt?: string): string | null {
  if (!expiresAt) return null;
  const date = new Date(expiresAt);
  if (Number.isNaN(date.getTime())) return null;
  return `Waiting for you until ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

/** What a settled request asked and how it was answered, for the expanded line. */
function Details({ request, part }: { request: HitlRequestSpec; part: HitlPart }) {
  const edited = part.answer?.edited as Record<string, string> | undefined;
  const reason = typeof part.answer?.reason === "string" ? part.answer.reason : null;
  return (
    <div className="mt-2 rounded-lg border border-border bg-muted/30 p-3 text-sm">
      <p className="font-medium break-words">{request.title}</p>
      {"detail" in request && request.detail ? (
        <p className="mt-1 text-muted-foreground break-words">{request.detail}</p>
      ) : null}
      {request.kind === "approval" && (edited || request.action) ? (
        <p className="mt-2 text-muted-foreground whitespace-pre-wrap break-words">
          {edited ? Object.values(edited).join("\n") : request.action}
        </p>
      ) : null}
      {reason ? <p className="mt-2 text-destructive">{reason}</p> : null}
    </div>
  );
}

/**
 * One human-in-the-loop request in the turn.
 *
 * While it waits and the turn is live it shows the kind's body; once settled it
 * collapses to one line ("You chose: Net revenue") that expands to what was
 * asked. A request the run can no longer receive (stopped, expired, replayed)
 * never offers its controls.
 */
export function HitlRequest({
  part,
  live,
  onAnswer,
}: {
  part: HitlPart;
  /** The run is still streaming, so an answer can reach it. */
  live: boolean;
  onAnswer: (part: HitlPart, body: HitlAnswerBody) => Promise<void>;
}) {
  const titleId = useId();
  const detailsId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const definition = hitlKind(part.kind);
  const request = part.request;

  // A stored approval from an earlier version is a past step: never answerable.
  const answerable = live && !(request?.kind === "approval" && request.legacy);

  if (part.status === "pending" && answerable && request && definition) {
    const { Body, Icon } = definition;
    const submit = async (body: HitlAnswerBody) => {
      if (busy) return;
      setBusy(true);
      setError(null);
      try {
        await onAnswer(part, body);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "The answer could not be sent. Try again.");
        setBusy(false);
      }
    };
    const destructive = request.kind === "approval" && request.classification === "destructive";
    const expiry = expiryText(part.expiresAt);
    return (
      <section
        aria-labelledby={titleId}
        data-testid="hitl-request"
        data-kind={part.kind}
        className={cn(
          "nova-chat-item rounded-xl border p-3 sm:p-4",
          request.kind === "approval"
            ? "border-warning/30 bg-warning/10 dark:border-warning/35 dark:bg-warning/15"
            : "border-border bg-card",
          destructive && "border-destructive/40 bg-destructive/5 dark:bg-destructive/10",
        )}
      >
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5 font-medium">
            <Icon aria-hidden="true" className={cn("size-4", request.kind === "approval" ? "text-warning-strong" : "text-primary")} />
            {definition.label}
          </span>
          {expiry ? <span>{expiry}</span> : null}
        </div>
        <h3 id={titleId} className="mt-2 text-sm font-medium break-words">
          {request.title}
        </h3>
        {"detail" in request && request.detail ? (
          <p className="mt-1 text-sm text-muted-foreground break-words">{request.detail}</p>
        ) : null}
        <Body request={request as never} submit={submit} busy={busy} titleId={titleId} />
        {busy && !error ? (
          <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
            <Loader2 aria-hidden="true" className="size-3 animate-spin" />
            Sending your answer…
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="mt-2 text-xs text-destructive">
            {error}
          </p>
        ) : null}
        {definition.skippable ? (
          <Button
            type="button"
            variant="link"
            size="sm"
            className="mt-1 h-auto px-0 text-xs text-muted-foreground"
            disabled={busy}
            onClick={() => void submit({ action: "decline" })}
          >
            Skip, let the agent assume
          </Button>
        ) : null}
      </section>
    );
  }

  if (part.status === "pending") {
    // Waiting for the request itself (it follows its part on the stream), or a
    // replayed request whose run is gone: nothing can be answered here.
    return answerable ? (
      <p className="nova-chat-item inline-flex items-center gap-1.5 text-sm text-muted-foreground" role="status">
        <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
        Preparing a question for you…
      </p>
    ) : null;
  }

  const StatusIcon = statusIcon(part.status);
  const negative = part.status === "denied" || part.status === "refused";
  const summary = part.summary || DEFAULT_SUMMARY[part.status];
  return (
    <div className="nova-chat-item" data-testid="hitl-summary" data-status={part.status}>
      <button
        type="button"
        className="inline-flex max-w-full items-start gap-1.5 rounded-md py-0.5 text-left text-sm text-muted-foreground hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none disabled:hover:text-muted-foreground"
        aria-expanded={open}
        aria-controls={detailsId}
        disabled={!request}
        onClick={() => setOpen((value) => !value)}
      >
        <StatusIcon aria-hidden="true" className={cn("mt-0.5 size-3.5 shrink-0", negative ? "text-destructive" : undefined)} />
        <span className="min-w-0 break-words">{summary}</span>
        {request ? (
          <ChevronDown aria-hidden="true" className={cn("mt-0.5 size-3.5 shrink-0 transition-transform", open && "rotate-180")} />
        ) : null}
      </button>
      {open && request ? (
        <div id={detailsId}>
          <Details request={request} part={part} />
        </div>
      ) : null}
    </div>
  );
}
