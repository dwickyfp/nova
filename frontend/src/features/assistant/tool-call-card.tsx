import { useState } from "react";
import { AlertTriangle, Ban, Check, Loader2, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { StatusBadge, type StatusTone } from "@/components/ui/status-badge";
import { cn } from "@/lib/utils";
import type { ToolCallView, ToolCallStatus } from "./types";

const STATUS_TONE: Record<ToolCallStatus, StatusTone> = {
  pending: "warning",
  approved: "info",
  denied: "neutral",
  running: "info",
  done: "success",
  failed: "danger",
  cancelled: "neutral",
};

const STATUS_LABEL: Record<ToolCallStatus, string> = {
  pending: "Awaiting approval",
  approved: "Approved",
  denied: "Denied",
  running: "Running",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

export type ToolCallDecision = {
  toolCallId: string;
  decision: "approve" | "deny";
  alwaysAllow: boolean;
};

export type ToolCallCardProps = {
  toolCall: ToolCallView;
  /** Present once the card has a server-side id to decide against. */
  toolCallId?: string;
  onDecide?: (decision: ToolCallDecision) => void;
  /** Disables the controls while a decision is in flight. */
  busy?: boolean;
};

export function ToolCallCard({
  toolCall,
  toolCallId,
  onDecide,
  busy,
}: ToolCallCardProps) {
  const [alwaysAllow, setAlwaysAllow] = useState(false);
  const canOfferAlwaysAllow = toolCall.classification === "read_only";
  const isDecidable =
    toolCall.status === "pending" && Boolean(onDecide && toolCallId);

  const decide = (decision: "approve" | "deny") => {
    if (!toolCallId) return;
    onDecide?.({
      toolCallId,
      decision,
      alwaysAllow: alwaysAllow && canOfferAlwaysAllow,
    });
  };

  return (
    <div
      data-slot="tool-call-card"
      data-classification={toolCall.classification}
      className={cn(
        "rounded-lg border bg-surface-2 p-3",
        toolCall.classification === "denied" && "border-destructive/35",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <code className="truncate font-mono text-xs text-foreground">
          {toolCall.tool_name}
        </code>
        <StatusBadge
          tone={STATUS_TONE[toolCall.status]}
          dot={toolCall.status === "running"}
        >
          {toolCall.status === "running" ? (
            <Loader2 aria-hidden="true" className="size-3 animate-spin" />
          ) : null}
          {STATUS_LABEL[toolCall.status]}
        </StatusBadge>
      </div>

      <pre className="mt-2 max-h-40 overflow-auto rounded-md border bg-surface-1 p-2 text-xs whitespace-pre-wrap text-foreground">
        {toolCall.sql_preview}
      </pre>

      {toolCall.classification !== "read_only" ? (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-warning-strong">
          <ShieldAlert aria-hidden="true" className="size-3.5 shrink-0" />
          This statement changes data. It is never covered by an always-allow
          grant.
        </p>
      ) : null}

      {toolCall.result_summary ? (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Check
            aria-hidden="true"
            className="size-3.5 shrink-0 text-success-strong"
          />
          {toolCall.result_summary}
        </p>
      ) : null}

      {toolCall.error ? (
        <p
          role="alert"
          className="mt-2 flex items-start gap-1.5 text-xs text-destructive"
        >
          <AlertTriangle
            aria-hidden="true"
            className="mt-0.5 size-3.5 shrink-0"
          />
          {toolCall.error}
        </p>
      ) : null}

      {isDecidable ? (
        <div className="mt-3 space-y-2 border-t pt-3">
          {canOfferAlwaysAllow ? (
            <label className="flex min-h-11 cursor-pointer items-center gap-2 text-xs text-muted-foreground">
              <Checkbox
                checked={alwaysAllow}
                onCheckedChange={(value) => setAlwaysAllow(value === true)}
                disabled={busy}
              />
              Always allow read-only queries in this conversation
            </label>
          ) : null}
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => decide("deny")}
            >
              <Ban aria-hidden="true" className="size-4" />
              Deny
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={busy}
              onClick={() => decide("approve")}
            >
              <Check aria-hidden="true" className="size-4" />
              Allow
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
