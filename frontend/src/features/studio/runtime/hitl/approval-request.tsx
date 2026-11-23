import { useId, useState } from "react";
import { Database, Eye, Loader2, Pencil, RefreshCcw, TriangleAlert, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { HitlBodyProps } from "./registry";
import type { ApprovalRequest, HitlAnswerBody } from "./types";

const CLASS_LABEL: Record<ApprovalRequest["classification"], { text: string; Icon: typeof Eye }> = {
  read_only: { text: "Read-only", Icon: Eye },
  session_change: { text: "Switches your role", Icon: RefreshCcw },
  destructive: { text: "Changes data", Icon: TriangleAlert },
};

const REASON: Record<ApprovalRequest["reason"], string> = {
  asks_first: "This agent asks before each data step. Allow it once, or for the rest of this chat.",
  changes_data: "It changes data, so it always needs your approval.",
  changes_role: "It switches the active role for this chat, so it needs your approval.",
};

/** The value Edit then run starts from: the request as it would run. */
function editableValue(request: ApprovalRequest): string {
  if (request.previewKind === "semantic") return request.semantic?.question ?? request.preview;
  return request.preview;
}

function Preview({ request }: { request: ApprovalRequest }) {
  if (request.previewKind === "semantic") {
    return (
      <div className="mt-3 rounded-lg border border-border bg-muted/40 p-3 text-sm">
        <p className="text-xs font-medium text-muted-foreground">Semantic request</p>
        <dl className="mt-1.5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
          {request.semantic?.view ? (
            <>
              <dt className="text-muted-foreground">Semantic View</dt>
              <dd className="break-words">{request.semantic.view}</dd>
            </>
          ) : null}
          <dt className="text-muted-foreground">Question</dt>
          <dd className="break-words">{request.semantic?.question ?? request.preview}</dd>
        </dl>
        <p className="mt-2 text-xs text-muted-foreground">
          Nova builds the SQL from this view and runs it read-only. The exact SQL appears on the step after it runs.
        </p>
      </div>
    );
  }
  if (request.previewKind === "sql") {
    return (
      <div className="mt-3">
        <p className="text-xs font-medium text-muted-foreground">SQL that will run</p>
        <pre className="mt-1 max-h-48 overflow-auto rounded-lg bg-muted/40 p-2.5 text-xs leading-relaxed">
          <code className="font-mono whitespace-pre-wrap break-words">{request.preview}</code>
        </pre>
      </div>
    );
  }
  return request.preview ? <p className="mt-2 text-sm break-words">{request.preview}</p> : null;
}

export function ApprovalBody({ request, submit, busy, titleId }: HitlBodyProps<ApprovalRequest>) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => editableValue(request));
  const [sent, setSent] = useState<HitlAnswerBody["action"] | "edit" | null>(null);
  const editorId = useId();
  const noteId = useId();
  const destructive = request.classification === "destructive";
  const kind = CLASS_LABEL[request.classification];
  const objects = (request.objects ?? []).filter((item) => item !== request.semantic?.view);
  const edited = draft.trim();
  const unchanged = edited === editableValue(request).trim();

  const send = async (body: HitlAnswerBody, marker: HitlAnswerBody["action"] | "edit") => {
    setSent(marker);
    try {
      await submit(body);
    } finally {
      setSent(null);
    }
  };
  const spinner = (marker: HitlAnswerBody["action"] | "edit") =>
    sent === marker ? <Loader2 aria-hidden="true" className="size-3.5 animate-spin" /> : null;

  return (
    <div>
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
        {objects.map((item) => (
          <span key={item} className="inline-flex items-center gap-1">
            <Database aria-hidden="true" className="size-3.5" />
            {item}
          </span>
        ))}
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{REASON[request.reason]}</p>

      {editing && request.editable ? (
        <div className="mt-3">
          <label htmlFor={editorId} className="text-xs font-medium text-muted-foreground">
            {request.previewKind === "semantic" ? "Edit the question" : `Edit the ${request.editable.label}`}
          </label>
          <Textarea
            id={editorId}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={request.editable.maxLength}
            aria-describedby={noteId}
            spellCheck={request.editable.language !== "sql"}
            className={cn("mt-1 min-h-24 text-sm", request.editable.language === "sql" && "font-mono text-xs")}
            onKeyDown={(event) => {
              if (event.key === "Escape") setEditing(false);
            }}
            autoFocus
          />
          <p id={noteId} className="mt-1 text-xs text-muted-foreground">
            Nova checks the edited request against policy and your role before anything runs.
          </p>
        </div>
      ) : (
        <Preview request={request} />
      )}

      <div className="mt-3 flex flex-wrap gap-2" role="group" aria-labelledby={titleId}>
        {editing && request.editable ? (
          <>
            <Button
              type="button"
              size="sm"
              disabled={busy || !edited || unchanged}
              onClick={() =>
                void send(
                  { action: "allow_once", edited_arguments: { [request.editable!.argument]: edited } },
                  "edit",
                )
              }
            >
              {spinner("edit")}
              Run edited request
            </Button>
            <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(false)}>
              Cancel edit
            </Button>
          </>
        ) : (
          <>
            <Button
              type="button"
              size="sm"
              variant={destructive ? "outline" : "default"}
              disabled={busy}
              onClick={() => void send({ action: "allow_once" }, "allow_once")}
            >
              {spinner("allow_once")}
              Allow once
            </Button>
            {request.allowSession ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void send({ action: "allow_session" }, "allow_session")}
              >
                {spinner("allow_session")}
                Allow for this chat
              </Button>
            ) : null}
            {request.editable ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setDraft(editableValue(request));
                  setEditing(true);
                }}
              >
                <Pencil aria-hidden="true" className="size-3.5" />
                Edit then run
              </Button>
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => void send({ action: "deny" }, "deny")}
            >
              {spinner("deny")}
              Deny
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
