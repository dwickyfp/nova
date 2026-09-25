import { useCallback, useState } from "react";
import { Check, Copy, Loader2, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
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
  const [artifactId] = useState(
    () => `nove-code-${Date.now()}-${++codeCardSequence}`,
  );

  const canRun = Boolean(runnable && runContext);

  const run = useCallback(async () => {
    if (!canRun || status === "running") return;

    setStatus("running");
    setDetail(null);
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
    try {
      const results = await api.post<QueryResponse[]>("/query/execute", {
        sql: code,
        database: runContext?.database ?? null,
        schema: runContext?.schema ?? null,
        role: runContext?.role ?? null,
        max_rows: 500,
        confirm_destructive: false,
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
        status: feedback.execution.status === "success" ? "success" : "failure",
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
      setDetail(summarize(first));
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
    }
  }, [
    canRun,
    code,
    runContext?.database,
    runContext?.role,
    runContext?.schema,
    runContext?.appContext?.surface.id,
    runContext?.appContext?.editor?.documentId,
    onExecutionEvent,
    artifactId,
    status,
  ]);

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
