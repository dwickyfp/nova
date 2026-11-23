import { useState } from "react";
import { Eye, Loader2, RefreshCcw, TriangleAlert, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { HitlBodyProps } from "./registry";
import type { ApprovalRequest, HitlAnswerBody } from "./types";

const CLASS_LABEL: Record<ApprovalRequest["classification"], { text: string; Icon: typeof Eye }> = {
  read_only: { text: "Read-only", Icon: Eye },
  session_change: { text: "Switches your role", Icon: RefreshCcw },
  destructive: { text: "Changes data", Icon: TriangleAlert },
};

const REASON: Record<ApprovalRequest["reason"], string> = {
  changes_data: "It changes something outside this chat, so it needs your approval each time.",
  changes_role: "It switches the active role for this chat, so it needs your approval.",
};

/**
 * Approve one action the agent wants to take. Data queries run without asking
 * (your role already governs what they can read); only actions that change
 * something reach this card, and each is allowed or denied once.
 */
export function ApprovalBody({ request, submit, busy, titleId }: HitlBodyProps<ApprovalRequest>) {
  const [sent, setSent] = useState<HitlAnswerBody["action"] | null>(null);
  const kind = CLASS_LABEL[request.classification];
  const destructive = request.classification === "destructive";

  const send = async (action: "allow_once" | "deny") => {
    setSent(action);
    try {
      await submit({ action });
    } finally {
      setSent(null);
    }
  };
  const spinner = (action: HitlAnswerBody["action"]) =>
    sent === action ? <Loader2 aria-hidden="true" className="size-3.5 animate-spin" /> : null;

  return (
    <div>
      {request.action ? (
        <p className="mt-2 rounded-lg border border-border bg-background/60 p-2.5 text-sm break-words">{request.action}</p>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className={cn("inline-flex items-center gap-1", destructive && "text-destructive")}>
          <kind.Icon aria-hidden="true" className="size-3.5" />
          {kind.text}
        </span>
        {request.role ? (
          <span className="inline-flex items-center gap-1">
            <UserRound aria-hidden="true" className="size-3.5" />
            Runs as {request.role}
          </span>
        ) : null}
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{REASON[request.reason]}</p>
      <div className="mt-3 flex flex-wrap gap-2" role="group" aria-labelledby={titleId}>
        <Button type="button" size="sm" variant={destructive ? "outline" : "default"} disabled={busy} onClick={() => void send("allow_once")}>
          {spinner("allow_once")}
          Allow once
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void send("deny")}>
          {spinner("deny")}
          Deny
        </Button>
      </div>
    </div>
  );
}
