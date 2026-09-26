import { useEffect, useRef, useState } from "react";
import {
  Check,
  Copy,
  Cpu,
  RefreshCcw,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export type AnswerFeedback = "like" | "dislike" | null;

export function AnswerFooter({
  answer,
  model,
  tokens,
  inputTokens,
  outputTokens,
  feedback = null,
  onFeedback,
  onReconsider,
  reconsidering = false,
  reconsiderDisabled = false,
}: {
  answer: string;
  model?: string;
  tokens?: number;
  inputTokens?: number;
  outputTokens?: number;
  feedback?: AnswerFeedback;
  onFeedback?: (feedback: AnswerFeedback) => Promise<void>;
  onReconsider?: () => void;
  reconsidering?: boolean;
  reconsiderDisabled?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [visibleFeedback, setVisibleFeedback] = useState(feedback);
  const feedbackState = useRef({ confirmed: feedback, desired: feedback, writing: false });
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    const state = feedbackState.current;
    if (state.writing) return;
    state.confirmed = feedback;
    state.desired = feedback;
    setVisibleFeedback(feedback);
  }, [feedback]);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  useEffect(() => () => clearTimeout(copyTimer.current), []);

  async function copyAnswer() {
    try {
      await navigator.clipboard.writeText(answer);
      setCopied(true);
      clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Could not copy the answer.");
    }
  }

  async function rate(next: Exclude<AnswerFeedback, null>) {
    if (!onFeedback) return;
    const state = feedbackState.current;
    state.desired = state.desired === next ? null : next;
    setVisibleFeedback(state.desired);
    if (state.writing) return;
    state.writing = true;

    // Serialize writes and keep only the latest choice while a request is pending.
    while (true) {
      const requested = state.desired;
      try {
        await onFeedback(requested);
        state.confirmed = requested;
      } catch {
        if (state.desired === requested) {
          state.desired = state.confirmed;
          if (mounted.current) {
            setVisibleFeedback(state.confirmed);
            toast.error("Could not save feedback. Please try again.");
          }
          break;
        }
      }
      if (state.desired === requested) break;
    }
    state.writing = false;
  }

  const actions = [
    ...(answer
      ? [
          {
            label: copied ? "Copied" : "Copy answer",
            icon: copied ? Check : Copy,
            onClick: () => void copyAnswer(),
            disabled: false,
            pressed: undefined,
          },
        ]
      : []),
    {
      label: "Like answer",
      icon: ThumbsUp,
      onClick: () => void rate("like"),
      disabled: !onFeedback,
      pressed: visibleFeedback === "like",
    },
    {
      label: "Dislike answer",
      icon: ThumbsDown,
      onClick: () => void rate("dislike"),
      disabled: !onFeedback,
      pressed: visibleFeedback === "dislike",
    },
    ...(onReconsider
      ? [
          {
            label: reconsidering ? "Reconsidering" : "Reconsider this answer",
            icon: RefreshCcw,
            onClick: onReconsider,
            disabled: reconsiderDisabled || reconsidering,
            pressed: undefined,
          },
        ]
      : []),
  ];
  const hasUsage =
    tokens !== undefined ||
    inputTokens !== undefined ||
    outputTokens !== undefined;

  return (
    <div
      role="group"
      aria-label="Answer actions"
      className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground"
    >
      <div className="flex shrink-0 items-center gap-0.5">
        {actions.map(({ label, icon: Icon, onClick, disabled, pressed }) => (
          <Tooltip key={label} delayDuration={400}>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label={label}
                aria-pressed={pressed}
                disabled={disabled}
                onClick={onClick}
                className={cn(
                  "size-8 rounded-md hover:text-foreground",
                  pressed !== undefined && "bg-transparent hover:bg-transparent dark:hover:bg-transparent",
                  pressed && (Icon === ThumbsUp
                    ? "text-info-strong hover:text-info-strong"
                    : "text-destructive hover:text-destructive"),
                )}
              >
                <Icon
                  aria-hidden="true"
                  className={cn(
                    "size-4",
                    reconsidering && Icon === RefreshCcw && "animate-spin",
                  )}
                />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom" sideOffset={6}>
              {label}
            </TooltipContent>
          </Tooltip>
        ))}
      </div>
      {model || hasUsage ? (
        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              aria-label="Answer details"
              className="min-w-0 max-w-full gap-2 px-2 text-xs font-normal text-muted-foreground hover:text-foreground"
            >
              <Cpu aria-hidden="true" className="size-3.5 shrink-0" />
              {model ? <span className="truncate">{model}</span> : null}
              {tokens !== undefined ? (
                <span className="shrink-0 tabular-nums">
                  {tokens.toLocaleString()} tokens
                </span>
              ) : null}
            </Button>
          </PopoverTrigger>
          <PopoverContent
            align="start"
            side="top"
            sideOffset={8}
            className="max-w-[calc(100vw-2rem)] space-y-3 text-sm"
          >
            <p className="font-medium">Answer details</p>
            <dl className="space-y-2">
              {model ? (
                <div className="space-y-1">
                  <dt className="text-xs text-muted-foreground">Model</dt>
                  <dd className="break-words">{model}</dd>
                </div>
              ) : null}
              {(
                [
                  ["Input tokens", inputTokens],
                  ["Output tokens", outputTokens],
                  ["Total tokens", tokens],
                ] as const
              ).map(([label, value]) =>
                value !== undefined ? (
                  <div
                    key={label}
                    className="flex items-center justify-between gap-4 text-xs"
                  >
                    <dt className="text-muted-foreground">{label}</dt>
                    <dd className="tabular-nums">{value.toLocaleString()}</dd>
                  </div>
                ) : null,
              )}
              {!hasUsage ? (
                <p className="text-xs text-muted-foreground">
                  Token usage was not reported.
                </p>
              ) : null}
            </dl>
          </PopoverContent>
        </Popover>
      ) : null}
    </div>
  );
}
