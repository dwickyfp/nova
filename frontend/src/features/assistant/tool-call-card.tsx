import { useRef, useState } from "react";
import {
  AlertTriangle,
  Ban,
  Check,
  ChevronRight,
  Loader2,
  ShieldAlert,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { ToolCallView, ToolCallStatus } from "./types";

const STATUS_LABEL: Record<ToolCallStatus, string> = {
  pending: "Awaiting approval",
  approved: "Approved",
  denied: "Denied",
  running: "Running",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

const TOOL_LABEL: Record<string, string> = {
  query_execute: "Run query",
  semantic_query: "Query data",
  search_knowledge: "Check guidance",
  call_ui_operation: "Apply action",
  invoke_client_capability: "Update view",
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
  const [showDetails, setShowDetails] = useState(false);
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
  const detailsVisible = toolCall.status === "pending" || showDetails;
  const title =
    TOOL_LABEL[toolCall.tool_name] ?? toolCall.tool_name.replace(/_/g, " ");

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
        "min-w-0 border-l pl-3 text-xs text-muted-foreground",
        toolCall.status === "failed" && "border-destructive/60",
      )}
    >
      <div className="flex min-h-11 min-w-0 items-center gap-1.5 sm:min-h-7">
        {toolCall.status === "running" ? (
          <Loader2
            aria-hidden="true"
            className="size-3.5 shrink-0 animate-spin motion-reduce:animate-none"
          />
        ) : toolCall.status === "failed" ? (
          <AlertTriangle
            aria-hidden="true"
            className="size-3.5 shrink-0 text-destructive"
          />
        ) : toolCall.status === "done" ? (
          <Check
            aria-hidden="true"
            className="size-3.5 shrink-0 text-success-strong"
          />
        ) : null}
        <span className="shrink-0 font-medium text-foreground">{title}</span>
        <span aria-hidden="true">·</span>
        <span
          className={cn(
            "shrink-0",
            toolCall.status === "failed" && "text-destructive",
          )}
        >
          {STATUS_LABEL[toolCall.status]}
        </span>
        {toolCall.status !== "pending" ? (
          <button
            type="button"
            aria-label={`${detailsVisible ? "Hide" : "Show"} ${title} details`}
            aria-expanded={detailsVisible}
            onClick={() => setShowDetails((value) => !value)}
            className="ml-auto inline-flex size-11 shrink-0 items-center justify-center rounded-sm hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:size-8"
          >
            <ChevronRight
              aria-hidden="true"
              className={cn(
                "size-3.5 transition-transform",
                detailsVisible && "rotate-90",
              )}
            />
          </button>
        ) : null}
      </div>

      {detailsVisible ? (
        <div className="mt-1.5 space-y-1.5">
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-foreground">
            {toolCall.sql_preview}
          </pre>
          {!isDecidable ? (
            <p className="font-mono text-[11px]">{toolCall.tool_name}</p>
          ) : null}
        </div>
      ) : null}

      {isDecidable && toolCall.classification !== "read_only" ? (
        <p className="mt-1.5 flex items-center gap-1.5 text-warning-strong">
          <ShieldAlert aria-hidden="true" className="size-3.5 shrink-0" />
          {toolCall.classification === "session_change"
            ? "This statement changes your active role and requires approval."
            : "This statement changes data. It is never covered by an always-allow grant."}
        </p>
      ) : null}

      {toolCall.result_summary ? (
        <p className="mt-1 text-muted-foreground">{toolCall.result_summary}</p>
      ) : null}

      {toolCall.error ? (
        <p
          role="alert"
          className="mt-1 flex items-start gap-1.5 text-destructive"
        >
          <AlertTriangle
            aria-hidden="true"
            className="mt-0.5 size-3.5 shrink-0"
          />
          {toolCall.error}
        </p>
      ) : null}

      {isDecidable ? (
        <div className="mt-2 space-y-2">
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
              className="min-h-11 sm:min-h-0"
              disabled={busy}
              onClick={() => decide("deny")}
            >
              <Ban aria-hidden="true" className="size-4" />
              Deny
            </Button>
            <Button
              type="button"
              size="sm"
              className="min-h-11 sm:min-h-0"
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
