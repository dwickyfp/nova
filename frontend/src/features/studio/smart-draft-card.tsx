import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { FilePenLine } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import type { SmartDraft } from "@/features/assistant/types";
import { ontologyApi } from "@/features/ontology/api";
import { newspaperApi } from "./news/newspaper-api";

const COPY = {
  knowledge_proposal: {
    label: "Proposed definition change",
    waiting: "Nothing is filed yet. A domain steward reviews it after you submit.",
    done: "Submitted. Studio keeps the current definition until a steward approves it and a release is published.",
    action: "Submit for review",
  },
  investigation_update: {
    label: "Proposed investigation finding",
    waiting: "Nothing is posted yet. Another reader in your role reviews it after you submit.",
    done: "Posted for review. News shows it only after it is approved.",
    action: "Post for review",
  },
} as const;

/** The same draft always files under the same operation, so a retry is not a second proposal. */
async function operationId(payload: Record<string, unknown>): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return "draft-" + Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, 40);
}

/**
 * A proposal or finding Smart prepared. A Smart turn cannot ask for consent, so
 * nothing exists until the user submits it here; the server checks it again.
 */
export function SmartDraftCard({ draft }: { draft: SmartDraft }) {
  const [dismissed, setDismissed] = useState(false);
  const copy = COPY[draft.draft_kind];
  const submit = useMutation({
    mutationFn: async () => {
      if (draft.draft_kind === "knowledge_proposal") {
        await ontologyApi.propose({
          ...(draft.payload as Omit<Parameters<typeof ontologyApi.propose>[0], "operation_id">),
          operation_id: await operationId(draft.payload),
        });
      } else {
        await newspaperApi.submitFinding(draft.incident_id, draft.payload.draft);
      }
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "It could not be submitted. Try again."),
  });
  if (dismissed && !submit.isSuccess) return null;
  return (
    <section
      aria-label={copy.label}
      className="nova-chat-item rounded-xl border border-border bg-card px-4 py-3 text-sm text-card-foreground"
    >
      <div className="flex items-start gap-3">
        <FilePenLine aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
        <div className="min-w-0 flex-1 space-y-1">
          <p className="font-medium break-words">{draft.title}</p>
          {draft.detail ? <p className="text-muted-foreground break-words">{draft.detail}</p> : null}
          <p className="text-muted-foreground" aria-live="polite">
            {submit.isSuccess ? copy.done : copy.waiting}
          </p>
        </div>
      </div>
      {submit.isSuccess ? null : (
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={() => setDismissed(true)} disabled={submit.isPending}>
            Not now
          </Button>
          <Button type="button" size="sm" onClick={() => submit.mutate()} disabled={submit.isPending}>
            {submit.isPending ? "Submitting" : copy.action}
          </Button>
        </div>
      )}
    </section>
  );
}
