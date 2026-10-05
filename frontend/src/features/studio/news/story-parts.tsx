import { TrendingDown, TrendingUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { StatusBadge } from "@/components/ui/status-badge";
import { ApiError } from "@/lib/api-client";
import { TONE_TEXT, deltaLine, formatValue, tone, weekdayName } from "./format";
import type { Story } from "./newspaper-api";

export function NewsFailure({ error, retry }: { error: unknown; retry: () => void }) {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 403)
    return (
      <EmptyState
        title="News is not enabled for your account"
        description="An administrator switches News on per user under Users & Roles."
      />
    );
  return (
    <EmptyState
      variant="error"
      title={
        status === 404
          ? "This story is not available with your current access"
          : "The edition could not be loaded"
      }
      description={
        status === 404
          ? "It may have been revised, or your active role cannot read its data."
          : "Try again when the service is available."
      }
      action={
        <Button variant="outline" onClick={retry}>
          Retry
        </Button>
      }
    />
  );
}

export function Severity({ story }: { story: Story }) {
  return (
    <StatusBadge tone={story.severity === "critical" ? "danger" : "warning"}>
      {story.severity === "critical" ? "Critical" : "Material"}
    </StatusBadge>
  );
}

/**
 * The story's headline figure: what was observed, and beneath it how far that
 * is from typical, coloured by whether the move is good for the business.
 */
export function HeadFigure({ story, large = false }: { story: Story; large?: boolean }) {
  const Icon = story.change > 0 ? TrendingUp : TrendingDown;
  return (
    <div data-slot="head-figure">
      <p
        className={`font-semibold tracking-tight tabular-nums ${large ? "text-5xl" : "text-3xl"}`}
      >
        {formatValue(story.after, story.unit, true)}
      </p>
      <p className="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-sm">
        <span
          data-tone={tone(story)}
          className={`inline-flex items-center gap-1 font-semibold tabular-nums ${TONE_TEXT[tone(story)]}`}
        >
          <Icon aria-hidden="true" className="size-4" />
          <span className="sr-only">{story.change > 0 ? "Up" : "Down"}</span>
          {deltaLine(story)}
        </span>
        <span className="text-muted-foreground">
          vs typical {weekdayName(story)} of {formatValue(story.before, story.unit, true)}
        </span>
      </p>
    </div>
  );
}
