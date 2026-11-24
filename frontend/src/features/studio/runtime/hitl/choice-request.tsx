import { useRef, useState, type KeyboardEvent } from "react";
import { Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { HitlBodyProps } from "./registry";
import type { ChoiceRequest } from "./types";

/**
 * Pick one of a few described options (analysis plans, matching views, chart
 * variants). The cards are a radio group: arrows move, Space or a click picks,
 * and Continue sends the pick.
 */
export function ChoiceBody({ request, submit, busy, titleId }: HitlBodyProps<ChoiceRequest>) {
  const [picked, setPicked] = useState<string | null>(
    () => request.options.find((option) => option.recommended)?.id ?? null,
  );
  const [sending, setSending] = useState(false);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const focusIndex = Math.max(0, request.options.findIndex((option) => option.id === picked));

  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step = event.key === "ArrowDown" || event.key === "ArrowRight" ? 1
      : event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + request.options.length) % request.options.length;
    setPicked(request.options[next].id);
    refs.current[next]?.focus();
  };

  const send = async () => {
    if (!picked || busy) return;
    setSending(true);
    try {
      await submit({ action: "answer", answer: { option_id: picked } });
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="mt-3">
      <div role="radiogroup" aria-labelledby={titleId} className="grid gap-2 sm:grid-cols-2">
        {request.options.map((option, index) => {
          const selected = option.id === picked;
          return (
            <button
              key={option.id}
              ref={(node) => {
                refs.current[index] = node;
              }}
              type="button"
              role="radio"
              aria-checked={selected}
              tabIndex={index === focusIndex ? 0 : -1}
              disabled={busy}
              onClick={() => setPicked(option.id)}
              onKeyDown={(event) => move(event, index)}
              className={cn(
                "flex min-h-16 flex-col items-start gap-1 rounded-lg border border-border bg-background p-3 text-left text-sm transition-colors",
                "hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-60",
                selected && "border-brand bg-brand/5 hover:bg-brand/5",
              )}
            >
              <span className="flex w-full items-start gap-2">
                <span className="min-w-0 flex-1 font-medium break-words">{option.title}</span>
                <span
                  aria-hidden="true"
                  className={cn(
                    "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-input",
                    selected && "border-brand bg-brand text-brand-foreground",
                  )}
                >
                  {selected ? <Check className="size-3" /> : null}
                </span>
              </span>
              {option.description ? (
                <span className="text-muted-foreground break-words">{option.description}</span>
              ) : null}
              {option.recommended || option.badges?.length ? (
                <span className="mt-0.5 flex flex-wrap gap-1">
                  {option.recommended ? (
                    <span className="rounded-full bg-brand/10 px-2 py-0.5 text-xs text-brand">Suggested</span>
                  ) : null}
                  {option.badges?.map((badge) => (
                    <span key={badge} className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                      {badge}
                    </span>
                  ))}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      <Button type="button" size="sm" className="mt-3" disabled={busy || !picked} onClick={() => void send()}>
        {sending ? <Loader2 aria-hidden="true" className="size-3.5 animate-spin" /> : null}
        Continue with this option
      </Button>
    </div>
  );
}
