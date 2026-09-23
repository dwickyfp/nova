import { useRef, useState } from "react";
import { ArrowUp, FileCode, Info, Square, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ApprovalModeSelector } from "./approval-mode-selector";
import type { SelectedModel } from "./assistant-provider";
import { ModelSelector } from "./model-selector";
import type { AttachedQuery } from "./query-attach";
import type { ApprovalMode } from "./use-assistant-turn";

export type AssistantComposerProps = {
  onSendMessage?: (message: string) => void;
  /** True while a turn is streaming; swaps Send for Stop. */
  streaming?: boolean;
  onStop?: () => void;
  disabled?: boolean;
  selectedModel?: SelectedModel;
  onSelectModel?: (model: SelectedModel) => void;
  /** How the next read-only query is approved; null hides the selector. */
  approvalMode?: ApprovalMode;
  onSelectApprovalMode?: (mode: ApprovalMode) => void;
  /** True while an approval-mode change is being persisted. */
  settlingApprovalMode?: boolean;
  /** Queries attached from the workspace, shown as badges above the textarea. */
  attachments?: AttachedQuery[];
  onRemoveAttachment?: (id: string) => void;
};

/**
 * The message composer. A single bordered card holds the auto-growing textarea
 * and a footer row with the model and approval-mode selectors and the send/stop
 * control, matching the familiar "prompt box" shape rather than a bare textarea
 * beside a button. The textarea uses `field-sizing-content`, so it grows with
 * the message up to a cap and then scrolls. Attached queries appear as removable
 * badges above the textarea, so an attachment is visible without occupying the
 * message itself.
 */
export function AssistantComposer({
  onSendMessage,
  streaming,
  onStop,
  disabled,
  selectedModel,
  onSelectModel,
  approvalMode,
  onSelectApprovalMode,
  settlingApprovalMode,
  attachments,
  onRemoveAttachment,
}: AssistantComposerProps) {
  const [value, setValue] = useState("");
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const canSend =
    Boolean(value.trim()) && !streaming && !disabled && Boolean(onSendMessage);
  const attached = attachments ?? [];

  const submit = () => {
    const message = value.trim();
    if (!message || streaming || disabled || !onSendMessage) return;
    onSendMessage(message);
    setValue("");
    fieldRef.current?.focus();
  };

  return (
    <div className="px-3 pt-3 pb-2">
      {disabled ? (
        <p className="mb-2 text-xs text-muted-foreground">
          The assistant backend is not connected yet. This panel is read-only
          until it is.
        </p>
      ) : null}
      <div
        className={cn(
          "flex flex-col gap-2 rounded-xl border bg-surface-2 p-2 shadow-xs",
          "focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/40",
          disabled && "opacity-60",
        )}
      >
        {attached.length > 0 ? (
          <div className="flex flex-wrap gap-1.5" aria-label="Attached queries">
            {attached.map((attachment) => (
              <span
                key={attachment.id}
                className="inline-flex max-w-full items-center gap-1 rounded-md border bg-surface-1 px-1.5 py-0.5 text-xs"
                title={attachment.sql}
              >
                <FileCode
                  aria-hidden="true"
                  className="size-3 shrink-0 text-muted-foreground"
                />
                <span className="truncate font-medium">
                  {attachment.fileName || "selection"}
                </span>
                <span className="shrink-0 text-muted-foreground">
                  {attachment.endLine - attachment.startLine + 1}
                  {" ln"}
                </span>
                {onRemoveAttachment ? (
                  <button
                    type="button"
                    className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label={`Remove ${attachment.fileName || "attached query"}`}
                    onClick={() => onRemoveAttachment(attachment.id)}
                  >
                    <X aria-hidden="true" className="size-3" />
                  </button>
                ) : null}
              </span>
            ))}
          </div>
        ) : null}
        <textarea
          ref={fieldRef}
          name="assistant-message"
          rows={1}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Ask a question or describe a query"
          disabled={disabled || !onSendMessage}
          className={cn(
            "field-sizing-content max-h-40 min-h-9 w-full resize-none bg-transparent px-1 py-1 text-sm",
            "outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed",
          )}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
        />
        <div className="flex items-center gap-2">
          {onSelectModel ? (
            <ModelSelector
              selected={selectedModel ?? null}
              onSelect={onSelectModel}
              disabled={disabled || streaming}
              align="start"
            />
          ) : null}
          {onSelectApprovalMode && approvalMode ? (
            <ApprovalModeSelector
              selected={approvalMode}
              onSelect={onSelectApprovalMode}
              settling={settlingApprovalMode}
              disabled={disabled || streaming}
              align="start"
            />
          ) : null}
          <div className="flex-1" />
          {streaming ? (
            <Button
              type="button"
              size="icon"
              variant="outline"
              className="size-8 rounded-full"
              onClick={onStop}
              aria-label="Stop generating"
            >
              <Square className="size-3.5" />
            </Button>
          ) : (
            <Button
              type="button"
              size="icon"
              className="size-8 rounded-full"
              disabled={!canSend}
              onClick={submit}
              aria-label="Send message"
            >
              <ArrowUp className="size-4" />
            </Button>
          )}
        </div>
      </div>
      <p className="mt-2 flex items-center justify-center gap-1.5 text-center text-xs leading-4 text-muted-foreground">
        <Info aria-hidden="true" className="size-3.5 shrink-0" />
        <span>Nove can make mistakes. Check important details.</span>
      </p>
    </div>
  );
}
