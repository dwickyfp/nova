import { TrendingDown, TrendingUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { StatusBadge } from "@/components/ui/status-badge";
import { ApiError } from "@/lib/api-client";
import { formatChange, formatValue } from "./format";
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

export function Movement({ story, large = false }: { story: Story; large?: boolean }) {
  const Icon = story.change > 0 ? TrendingUp : TrendingDown;
  const change = formatChange(story.relative_change);
  return (
    <span
      className={`inline-flex items-center gap-1.5 font-semibold tracking-tight tabular-nums ${large ? "text-3xl" : "text-xl"}`}
    >
      <Icon aria-hidden="true" className={large ? "size-6" : "size-5"} />
      <span className="sr-only">{story.change > 0 ? "Up" : "Down"}</span>
      {change ?? formatValue(story.after, story.unit, true)}
    </span>
  );
}
