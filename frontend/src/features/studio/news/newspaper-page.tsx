import { useQuery } from "@tanstack/react-query";
import { RefreshCw, TrendingDown, TrendingUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { StatusBadge } from "@/components/ui/status-badge";
import { ApiError } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import {
  byPriority,
  formatChange,
  formatValue,
  humanize,
  kicker,
  longDate,
} from "./format";
import { newspaperApi, type Newspaper, type Story } from "./newspaper-api";
import { Sparkline, TrendChart } from "./story-chart";
import { StoryPage } from "./story-page";

type Props = {
  story?: string;
  onOpen: (id?: string) => void;
  onAlerts: () => void;
  onFollowUp: (prompt: string) => void;
};

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
      className={`inline-flex items-center gap-1.5 font-semibold tabular-nums ${large ? "text-2xl" : "text-sm"}`}
    >
      <Icon aria-hidden="true" className={large ? "size-5" : "size-4"} />
      <span className="sr-only">{story.change > 0 ? "Up" : "Down"}</span>
      {change ?? formatValue(story.after, story.unit, true)}
    </span>
  );
}

function Lead({ story, onOpen }: { story: Story; onOpen: Props["onOpen"] }) {
  return (
    <article aria-labelledby="lead-headline" className="min-w-0">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Severity story={story} />
        <span>{kicker(story)}</span>
      </div>
      <h2
        id="lead-headline"
        className="mt-3 text-2xl font-semibold tracking-tight text-balance sm:text-3xl"
      >
        <button
          type="button"
          className="rounded-sm text-left hover:underline focus-visible:outline focus-visible:outline-ring"
          onClick={() => onOpen(story.id)}
        >
          {story.narrative.headline}
        </button>
      </h2>
      <p className="mt-2 text-base text-muted-foreground">{story.narrative.deck}</p>
      <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-3">
        <div>
          <dt className="text-xs text-muted-foreground">Change</dt>
          <dd><Movement story={story} large /></dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Observed</dt>
          <dd className="text-2xl font-semibold tabular-nums">
            {formatValue(story.after, story.unit, true)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Typical</dt>
          <dd className="text-2xl font-semibold tabular-nums">
            {formatValue(story.before, story.unit, true)}
          </dd>
        </div>
      </dl>
      <div className="mt-4">
        <TrendChart story={story} height={220} />
      </div>
      <p className="mt-4 text-sm">{story.narrative.what_happened}</p>
      <Button variant="outline" className="mt-4" onClick={() => onOpen(story.id)}>
        Read the full story
      </Button>
    </article>
  );
}

function Card({ story, onOpen }: { story: Story; onOpen: Props["onOpen"] }) {
  return (
    <article className="flex min-w-0 flex-col border-t pt-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Severity story={story} />
        <span className="min-w-0 break-words">{kicker(story)}</span>
      </div>
      <h3 className="mt-2 text-base font-semibold tracking-tight text-balance">
        <button
          type="button"
          className="rounded-sm text-left hover:underline focus-visible:outline focus-visible:outline-ring"
          onClick={() => onOpen(story.id)}
        >
          {story.narrative.headline}
        </button>
      </h3>
      <p className="mt-1 text-sm text-muted-foreground">{story.narrative.deck}</p>
      <div className="mt-3 flex items-end justify-between gap-3">
        <Movement story={story} />
        <Sparkline story={story} />
      </div>
    </article>
  );
}

function Edition({ paper, onOpen }: { paper: Newspaper; onOpen: Props["onOpen"] }) {
  const all = paper.sections.flatMap((section) => section.stories).sort(byPriority);
  const [lead] = all;
  if (!lead)
    return (
      <EmptyState
        title="Nothing material in this edition"
        description="No metric you can read moved beyond its threshold. The next edition is pressed on the view's schedule."
      />
    );
  return (
    <>
      <div className="grid gap-8 lg:grid-cols-12">
        <div className="min-w-0 lg:col-span-8">
          <Lead story={lead} onOpen={onOpen} />
        </div>
        <aside
          aria-labelledby="edition-index"
          className="min-w-0 lg:col-span-4 lg:border-l lg:pl-8"
        >
          <h2 id="edition-index" className="text-xs font-medium text-muted-foreground">
            In this edition
          </h2>
          <ol className="mt-2 divide-y">
            {all.map((story) => (
              <li key={story.id}>
                <button
                  type="button"
                  className="flex w-full items-start justify-between gap-3 py-3 text-left hover:bg-muted/50 focus-visible:outline focus-visible:outline-ring"
                  onClick={() => onOpen(story.id)}
                >
                  <span className="min-w-0">
                    <span className="block text-xs text-muted-foreground">
                      {kicker(story)}
                    </span>
                    <span className="block text-sm font-medium break-words">
                      {story.narrative.headline}
                    </span>
                  </span>
                  <Movement story={story} />
                </button>
              </li>
            ))}
          </ol>
        </aside>
      </div>
      {paper.sections.map((section) => {
        const rest = section.stories.filter((story) => story.id !== lead.id).sort(byPriority);
        if (!rest.length) return null;
        return (
          <section key={section.view_id} aria-labelledby={`section-${section.view_id}`} className="mt-10">
            <div className="flex flex-wrap items-baseline justify-between gap-2 border-t-2 border-foreground pt-2">
              <h2 id={`section-${section.view_id}`} className="text-sm font-semibold">
                {humanize(section.name)}
              </h2>
              <p className="text-xs text-muted-foreground">
                {rest.length} more {rest.length === 1 ? "story" : "stories"}
              </p>
            </div>
            <div className="mt-4 grid gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-3">
              {rest.map((story) => (
                <Card key={story.id} story={story} onOpen={onOpen} />
              ))}
            </div>
          </section>
        );
      })}
    </>
  );
}

export function NewspaperPage({ story, onOpen, onAlerts, onFollowUp }: Props) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const paper = useQuery({
    queryKey: ["newspaper", epoch],
    queryFn: ({ signal }) => newspaperApi.read(signal),
    enabled: !story,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  if (story)
    return (
      <StoryPage
        key={`${epoch}:${story}`}
        id={story}
        epoch={epoch}
        onBack={() => onOpen()}
        onFollowUp={onFollowUp}
      />
    );
  const pressed = paper.data?.sections
    .map((section) => section.pressed_at)
    .sort()
    .pop();
  return (
    <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6">
        <header className="border-b-2 border-foreground pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h1 className="text-3xl font-semibold tracking-tight">News</h1>
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="sm" onClick={onAlerts}>
                Monitor alerts
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Refresh the edition"
                onClick={() => void paper.refetch()}
              >
                <RefreshCw className="size-4" />
              </Button>
            </div>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {paper.data?.edition_date
              ? `${longDate(paper.data.edition_date)} · ${paper.data.sections.length} ${paper.data.sections.length === 1 ? "view" : "views"} covered${pressed ? ` · Pressed ${new Date(pressed).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" })}` : ""}`
              : "Material changes in the business, from the Semantic Views you can read."}
          </p>
        </header>
        <div className="mt-6">
          {paper.isError ? (
            <NewsFailure error={paper.error} retry={() => void paper.refetch()} />
          ) : paper.isFetching ? (
            <LoadingLines />
          ) : paper.data && paper.data.sections.length ? (
            <Edition paper={paper.data} onOpen={onOpen} />
          ) : (
            <EmptyState
              title="No edition to read yet"
              description="News appears after it is switched on for a Semantic View you can read and the first edition is pressed."
            />
          )}
        </div>
      </div>
    </div>
  );
}
