import { useId, useState } from "react";
import { Loader2, SendHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { HitlBodyProps } from "./registry";
import type { QuestionRequest } from "./types";

/** A clarifying question: suggested answers as chips, and/or the user's own words. */
export function QuestionBody({ request, submit, busy, titleId }: HitlBodyProps<QuestionRequest>) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState<string | null>(null);
  const inputId = useId();

  const answer = async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed || busy) return;
    setSending(trimmed);
    try {
      await submit({ action: "answer", answer: { text: trimmed } });
    } finally {
      setSending(null);
    }
  };

  return (
    <div className="mt-3 flex flex-col gap-3">
      {request.choices.length ? (
        <div className="flex flex-wrap gap-2" role="group" aria-labelledby={titleId}>
          {request.choices.map((choice) => (
            <Button
              key={choice}
              type="button"
              size="sm"
              variant="outline"
              className="h-auto min-h-8 rounded-full py-1.5 whitespace-normal text-left"
              disabled={busy}
              onClick={() => void answer(choice)}
            >
              {sending === choice ? <Loader2 aria-hidden="true" className="size-3.5 animate-spin" /> : null}
              {choice}
            </Button>
          ))}
        </div>
      ) : null}
      {request.allowFreeText ? (
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void answer(text);
          }}
        >
          <label htmlFor={inputId} className="sr-only">
            Your answer
          </label>
          <Input
            id={inputId}
            value={text}
            maxLength={1000}
            placeholder={request.choices.length ? "Or type your own answer" : "Type your answer"}
            disabled={busy}
            onChange={(event) => setText(event.target.value)}
            className="h-9 text-sm"
          />
          <Button type="submit" size="sm" disabled={busy || !text.trim()} aria-label="Send answer">
            {sending === text.trim() && sending ? (
              <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
            ) : (
              <SendHorizontal aria-hidden="true" className="size-3.5" />
            )}
          </Button>
        </form>
      ) : null}
    </div>
  );
}
