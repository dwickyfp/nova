import { useCallback, useRef, useState } from "react";
import { Check, Copy, Loader2, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { api } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import type { QueryResponse } from "@/features/workspaces/types";
import {
  safeNoveSql,
  summarizeQueryForNove,
} from "@/features/workspaces/nove-feedback";
import type { NoveEventInput } from "./app-context";
import type { TurnContext } from "./stream-client";

export type CodeCardStatus = "idle" | "running" | "success" | "error";
let codeCardSequence = 0;

export type CodeCardProps = {
  code: string;
  language: string;
  /** Rendered highlighted HTML, or null when the language is not supported. */
  highlighted: string | null;
  /** Database/schema/role to run against; omitted hides the Run control. */
  runContext?: TurnContext;
  /** Whether this is a SQL card (the only kind that can be run). */
  runnable?: boolean;
  onExecutionEvent?: (event: NoveEventInput) => void;
  onFixWithNove?: (prompt: string) => void;
};

function useCopy() {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }, []);
  return { copied, copy };
}

const STATUS_LABEL: Record<CodeCardStatus, string> = {
  idle: "Not run",
  running: "Running",
  success: "Success",
  error: "Failed",
};

export function CodeCard({
  code,
  language,
  highlighted,
  runContext,
  runnable,
  onExecutionEvent,
  onFixWithNove,
}: CodeCardProps) {
  const { copied, copy } = useCopy();
  const [status, setStatus] = useState<CodeCardStatus>("idle");
  const [detail, setDetail] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [table, setTable] = useState<QueryResponse | null>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const runningRef = useRef(false);
  const needsPassword = code.includes("'<temporary_password>'");
  const needsConfirmation = /\b(DROP|TRUNCATE|DELETE|UPDATE|REVOKE)\b/i.test(
    code,
  );
  const [artifactId] = useState(
    () => `nove-code-${Date.now()}-${++codeCardSequence}`,
  );

  const canRun = Boolean(runnable && runContext);

  const run = useCallback(
    async (confirmed = false, password?: string) => {
      if (!canRun || runningRef.current) return;
      if ((needsPassword || needsConfirmation) && !confirmed) {
        setConfirmOpen(true);
        return;
      }
      if (needsPassword && !password) return;
      if (
        /<[A-Za-z_][\w. -]*>/.test(
          needsPassword
            ? code.split("'<temporary_password>'").join("''")
            : code,
        )
      ) {
        setStatus("error");
        setDetail("Replace the remaining SQL placeholders before running.");
        return;
      }
      runningRef.current = true;

      setStatus("running");
      setDetail(null);
      setTable(null);
      const startedAt = Date.now();
      const executionId = `nove-card-${startedAt}`;
      const surfaceId = runContext?.appContext?.surface.id ?? "nova.global";
      const documentId = runContext?.appContext?.editor?.documentId;
      const safeSql = safeNoveSql(code);
      const evidence = {
        executionId,
        ...(documentId ? { documentId } : {}),
        ...(safeSql ? { sql: safeSql } : { sqlOmitted: true }),
      };
      try {
        onExecutionEvent?.({
          source: "user",
          type: "assistant_artifact_run",
          surfaceId,
          artifactId,
          executionId,
          payload: evidence,
        });
        onExecutionEvent?.({
          source: "execution",
          type: "query_started",
          surfaceId,
          artifactId,
          executionId,
          payload: evidence,
        });
        const results = await api.post<QueryResponse[]>("/query/execute", {
          sql: code,
          ...(needsPassword ? { temporary_password: password } : {}),
          database: runContext?.database ?? null,
          schema: runContext?.schema ?? null,
          role: runContext?.role ?? null,
          max_rows: 500,
          confirm_destructive: confirmed,
        });
        const first = results?.[0];
        const feedback = summarizeQueryForNove(
          executionId,
          code,
          results ?? [],
          Date.now() - startedAt,
        );
        onExecutionEvent?.({
          source: "execution",
          type: feedback.eventType,
          surfaceId,
          artifactId,
          executionId,
          status:
            feedback.execution.status === "success" ? "success" : "failure",
          payload: {
            ...feedback.eventPayload,
            ...(documentId ? { documentId } : {}),
          },
        });
        const failed = results?.find((result) => !result.success);
        if (!first || failed) {
          setStatus("error");
          setDetail(
            failed?.error
              ? (safeNoveSql(failed.error) ?? "Error details omitted.")
              : "The statement failed.",
          );
          return;
        }
        setStatus("success");
        setDetail(
          results.length > 1
            ? `${results.length} statements completed. ${summarize(results[results.length - 1])}`
            : summarize(first),
        );
        setTable(results.find((result) => result.columns?.length > 0) ?? null);
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "The statement could not be run.";
        onExecutionEvent?.({
          source: "execution",
          type: "query_failed",
          surfaceId,
          artifactId,
          executionId,
          status: "failure",
          payload: {
            ...evidence,
            errorMessage: safeNoveSql(message) ?? "Error details omitted.",
          },
        });
        setStatus("error");
        setDetail(safeNoveSql(message) ?? "Error details omitted.");
      } finally {
        runningRef.current = false;
      }
    },
    [
      canRun,
      code,
      runContext?.database,
      runContext?.role,
      runContext?.schema,
      runContext?.appContext?.surface.id,
      runContext?.appContext?.editor?.documentId,
      onExecutionEvent,
      artifactId,
      needsPassword,
      needsConfirmation,
    ],
  );

  return (
    <div className="my-2 overflow-hidden rounded-md border border-border/70 bg-surface-1">
      <pre className="overflow-x-auto p-3 text-xs">
        {highlighted ? (
          <code
            className="hljs font-mono"
            // Highlighted markup comes from the fixed language grammars.
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        ) : (
          <code className="font-mono">{code}</code>
        )}
      </pre>

      <div className="flex items-center gap-1 border-t border-border/60 px-2 py-1">
        <span className="flex-1 truncate font-mono text-[11px] text-muted-foreground">
          {language || "text"}
        </span>

        {canRun ? <StatusText status={status} /> : null}

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="size-11 sm:size-7"
              aria-label="Copy code"
              onClick={() => void copy(code)}
            >
              {copied ? (
                <Check
                  aria-hidden="true"
                  className="size-3.5 text-success-strong"
                />
              ) : (
                <Copy aria-hidden="true" className="size-3.5" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{copied ? "Copied" : "Copy"}</TooltipContent>
        </Tooltip>

        {canRun ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="size-11 sm:size-7"
                aria-label="Run statement"
                disabled={status === "running"}
                onClick={() => void run()}
              >
                {status === "running" ? (
                  <Loader2
                    aria-hidden="true"
                    className="size-3.5 animate-spin"
                  />
                ) : (
                  <Play aria-hidden="true" className="size-3.5" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>Run</TooltipContent>
          </Tooltip>
        ) : null}
      </div>

      {detail ? (
        <div
          role={status === "error" ? "alert" : "status"}
          className={cn(
            "border-t px-2 py-1 text-xs",
            status === "error" ? "text-destructive" : "text-muted-foreground",
          )}
        >
          <p>{detail}</p>
          {status === "error" && onFixWithNove && safeNoveSql(code) ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-1"
              onClick={() =>
                onFixWithNove(
                  "Fix the failed SQL from this Nove code card. Explain the proposed change and verify it after I run it.",
                )
              }
            >
              Fix with Nove
            </Button>
          ) : null}
        </div>
      ) : null}
      {table ? (
        <div className="overflow-x-auto border-t p-2">
          <table className="w-full text-left text-xs">
            <thead>
              <tr>
                {table.columns.slice(0, 12).map((column, index) => (
                  <th key={index} className="px-2 py-1 font-medium">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.slice(0, 20).map((row, index) => (
                <tr key={index}>
                  {row.slice(0, 12).map((value, column) => (
                    <td key={column} className="border-t px-2 py-1">
                      {value === null ? "NULL" : String(value)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {table.rows.length > 20 || table.columns.length > 12 ? (
            <p className="mt-1 text-xs text-muted-foreground">
              Preview limited to 20 rows and 12 columns.
            </p>
          ) : null}
        </div>
      ) : null}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {needsPassword
                ? "Create user with a temporary password"
                : "Confirm SQL execution"}
            </DialogTitle>
            <DialogDescription>
              {needsPassword
                ? "Enter the temporary password privately. It is sent only for execution and is not added to the conversation."
                : "This SQL changes or removes data. Completed statements cannot be automatically undone."}
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              const password = passwordRef.current?.value;
              if (passwordRef.current) passwordRef.current.value = "";
              setConfirmOpen(false);
              void run(true, password);
            }}
          >
            {needsPassword ? (
              <div className="space-y-2">
                <Label htmlFor={`${artifactId}-password`}>
                  Temporary password
                </Label>
                <Input
                  id={`${artifactId}-password`}
                  ref={passwordRef}
                  type="password"
                  autoComplete="new-password"
                  required
                />
              </div>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => setConfirmOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit">Confirm and run</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function StatusText({ status }: { status: CodeCardStatus }) {
  return (
    <span
      data-status={status}
      className={cn(
        "text-[11px]",
        status === "idle" && "text-muted-foreground",
        status === "running" && "text-info-strong",
        status === "success" && "text-success-strong",
        status === "error" && "text-destructive",
      )}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

function summarize(result: QueryResponse): string {
  const parts: string[] = [];
  if (result.row_count)
    parts.push(`${result.row_count} row${result.row_count === 1 ? "" : "s"}`);
  if (result.affected_rows) parts.push(`${result.affected_rows} affected`);
  parts.push(`${Math.round(result.elapsed_ms)} ms`);
  return parts.join(" · ");
}
