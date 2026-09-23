import { useRef, useState } from "react";
import { AlertTriangle, Ban, Check, Loader2, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
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
  secureInput?: { password: string };
  uploadFile?: File;
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
  const passwordRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const canOfferAlwaysAllow = toolCall.classification === "read_only";
  const needsPassword =
    toolCall.tool_name === "call_ui_operation" &&
    toolCall.sql_preview.startsWith("POST /api/v1/users\n");
  const needsFile =
    toolCall.tool_name === "call_ui_operation" &&
    (/^POST \/api\/v1\/stages\/[^/\n]+\/files\n/.test(toolCall.sql_preview) ||
      /^POST \/api\/v1\/explorer\/databases\/[^/\n]+\/stages\/[^/\n]+\/files\n/.test(
        toolCall.sql_preview,
      ));
  const isDecidable =
    toolCall.status === "pending" && Boolean(onDecide && toolCallId);

  const decide = (decision: "approve" | "deny") => {
    if (!toolCallId) return;
    const passwordField = passwordRef.current;
    const fileField = fileRef.current;
    if (
      decision === "approve" &&
      needsPassword &&
      !passwordField?.reportValidity()
    ) {
      return;
    }
    if (decision === "approve" && needsFile && !fileField?.reportValidity()) {
      return;
    }
    const uploadFile = fileField?.files?.[0];
    if (
      decision === "approve" &&
      needsFile &&
      uploadFile &&
      uploadFile.size > 256 * 1024 * 1024
    ) {
      fileField.setCustomValidity("Choose a file smaller than 256 MB");
      fileField.reportValidity();
      return;
    }
    onDecide?.({
      toolCallId,
      decision,
      alwaysAllow: alwaysAllow && canOfferAlwaysAllow,
      ...(decision === "approve" && needsPassword && passwordField
        ? { secureInput: { password: passwordField.value } }
        : {}),
      ...(decision === "approve" && needsFile && uploadFile
        ? { uploadFile }
        : {}),
    });
    if (passwordField) passwordField.value = "";
    if (fileField) fileField.value = "";
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
          {toolCall.classification === "session_change"
            ? "This statement changes your active role and requires approval."
            : "This statement changes data. It is never covered by an always-allow grant."}
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
          {needsPassword ? (
            <label className="block space-y-1.5 text-xs text-muted-foreground">
              Password for the new user
              <Input
                ref={passwordRef}
                type="password"
                name="nove-new-user-password"
                autoComplete="new-password"
                required
                disabled={busy}
              />
            </label>
          ) : null}
          {needsFile ? (
            <label className="block space-y-1.5 text-xs text-muted-foreground">
              File to upload
              <Input
                ref={fileRef}
                type="file"
                required
                disabled={busy}
                onChange={(event) => event.currentTarget.setCustomValidity("")}
              />
            </label>
          ) : null}
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
