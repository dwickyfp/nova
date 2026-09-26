import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatDateTime, formatTime } from "@/lib/datetime";

export function UserMessageFooter({
  message,
  createdAt,
}: {
  message: string;
  createdAt?: string;
}) {
  const [copied, setCopied] = useState(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(copyTimer.current), []);

  const timestamp = createdAt && !/(Z|[+-]\d{2}:?\d{2})$/i.test(createdAt)
    ? `${createdAt.replace(" ", "T")}Z`
    : createdAt;
  const time = formatTime(timestamp, { hour: "numeric", minute: "2-digit", hour12: true }, "");
  const label = copied ? "Copied" : "Copy message";

  async function copyMessage() {
    try {
      await navigator.clipboard.writeText(message);
      setCopied(true);
      clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Could not copy the message.");
    }
  }

  return (
    <div className="flex items-center justify-end gap-2 text-xs text-muted-foreground">
      {time ? (
        <time dateTime={timestamp} title={formatDateTime(timestamp)} className="whitespace-nowrap tabular-nums">
          {time}
        </time>
      ) : null}
      {message ? (
        <Tooltip delayDuration={400}>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={label}
              onClick={() => void copyMessage()}
              className="size-11 rounded-md hover:text-foreground sm:size-8"
            >
              {copied ? <Check aria-hidden="true" className="size-4" /> : <Copy aria-hidden="true" className="size-4" />}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={6}>{label}</TooltipContent>
        </Tooltip>
      ) : null}
    </div>
  );
}
