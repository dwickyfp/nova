import { useState } from "react";
import { Loader2, ShieldQuestion } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ToolCallView } from "@/features/assistant/types";

/**
 * The approval card for a tool call that needs consent.
 *
 * Nova's backend already pauses a turn here and exposes
 * `POST /agents/{id}/tool-calls/{call}/decision`; nothing in the UI called it,
 * so an `ask_every_tool` agent would have waited forever for an answer that
 * could not be given. This is that missing half.
 *
 * The three choices are the contract's three: allow once, allow for the rest of
 * the conversation, or deny. Which one is safe to offer is decided by the
 * classification the backend sent, not by this component.
 */
export function ConsentCard({
  call,
  onDecide,
}: {
  call: ToolCallView;
  onDecide: (
    decision: "allow_once" | "allow_session" | "deny",
  ) => Promise<void>;
}) {
  const [pending, setPending] = useState<
    "allow_once" | "allow_session" | "deny" | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const destructive = call.classification === "destructive";

  const decide = async (decision: "allow_once" | "allow_session" | "deny") => {
    if (pending) return;
    setPending(decision);
    setError(null);
    try {
      await onDecide(decision);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "The decision could not be sent.",
      );
      setPending(null);
    }
  };

  return (
    <div className="nova-chat-item rounded-xl border border-warning/30 bg-warning/10 p-3 dark:border-warning/35 dark:bg-warning/15">
      <div className="flex items-start gap-2">
        <ShieldQuestion
          aria-hidden="true"
          className="mt-0.5 size-4 shrink-0 text-warning-strong"
        />
        <div className="min-w-0 flex-1">
          <p className="text-sm">
            The agent wants to run{" "}
            <span className="font-mono text-xs">{call.tool_name}</span>
            {destructive ? " which changes data." : "."}
          </p>
          {call.sql_preview ? (
            <pre className="mt-2 max-h-40 overflow-auto rounded-md bg-muted/40 p-2 text-xs leading-relaxed">
              <code className="font-mono">{call.sql_preview}</code>
            </pre>
          ) : null}

          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant={destructive ? "outline" : "default"}
              disabled={pending !== null}
              onClick={() => void decide("allow_once")}
            >
              {pending === "allow_once" ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : null}
              Allow once
            </Button>
            {destructive ? null : (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={pending !== null}
                onClick={() => void decide("allow_session")}
              >
                {pending === "allow_session" ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : null}
                Allow for this chat
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={pending !== null}
              onClick={() => void decide("deny")}
            >
              {pending === "deny" ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : null}
              Deny
            </Button>
          </div>

          {error ? (
            <p className={cn("mt-2 text-xs text-destructive")}>{error}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
