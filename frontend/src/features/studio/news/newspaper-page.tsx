import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { useAuthStore } from "@/stores/auth-store";
import { byPriority, humanize, kicker, longDate } from "./format";
import { Highlighted } from "./highlight";
import {
  newspaperApi,
  type Newspaper,
  type Reaction,
  type Story,
} from "./newspaper-api";
import { Reactions } from "./reactions";
import { chartKind } from "./chart-kind";
import { StoryChart, TrendChart } from "./story-chart";
import { StoryPanel } from "./story-panel";
import { HeadFigure, NewsFailure, Severity } from "./story-parts";

type Props = {
  story?: string;
  /** A past edition day; the newest edition when absent. */
  edition?: string;
  onEdition?: (edition?: string) => void;
  onOpen: (id?: string) => void;
  onAlerts: () => void;
  onFollowUp: (prompt: string) => void;
};
type React_ = (id: string, reaction: Reaction | null) => void;

/**
 * One story of the edition. The whole block opens the story; the headline is
 * the keyboard target, so the block itself stays out of the tab order.
 */
function Entry({
  story,
  position,
  onOpen,
  onReact,
}: {
  story: Story;
  position: number;
  onOpen: Props["onOpen"];
  onReact: React_;
}) {
  const lead = position === 0;
  const Heading = lead ? "h2" : "h3";
  return (
    <article
      data-slot="news-entry"
      className="group -mx-4 cursor-pointer border-t px-4 py-10 transition-colors first:border-t-0 hover:bg-muted/40 sm:-mx-6 sm:px-6"
      onClick={() => onOpen(story.id)}
    >
      {lead && (
        <p className="mb-4 text-xs font-medium tracking-wide text-primary uppercase">
          Top story for you
          {story.reason ? (
            <span className="text-muted-foreground normal-case"> · {story.reason}</span>
          ) : null}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          {kicker(story)}
        </span>
        <Severity story={story} />
      </div>
      <Heading
        className={`mt-3 font-semibold tracking-tight text-balance ${lead ? "text-3xl sm:text-4xl" : "text-xl sm:text-2xl"}`}
      >
        <button
          type="button"
          className="rounded-sm text-left group-hover:underline focus-visible:outline focus-visible:outline-ring"
          onClick={(event) => {
            event.stopPropagation();
            onOpen(story.id);
          }}
        >
          {story.narrative.headline}
        </button>
      </Heading>
      {lead ? (
        <>
          <p className="mt-2 text-lg text-muted-foreground">{story.narrative.deck}</p>
          <div className="mt-6">
            <HeadFigure story={story} large />
          </div>
          <div className="mt-6">
            <TrendChart story={story} height={260} detailed={false} />
          </div>
          <p className="mt-5 text-sm leading-relaxed">
            <Highlighted text={story.narrative.what_happened} story={story} />
          </p>
        </>
      ) : (
        <div className="mt-4 grid items-start gap-6 lg:grid-cols-5">
          <div className="min-w-0 lg:col-span-3">
            <HeadFigure story={story} />
            <p className="mt-4 text-sm leading-relaxed">
              <Highlighted text={story.narrative.what_happened} story={story} />
            </p>
          </div>
          <div className="min-w-0 lg:col-span-2">
            <StoryChart story={story} kind={chartKind(story, position)} />
          </div>
        </div>
      )}
      <div className="mt-5 flex items-center justify-between gap-3">
        <Reactions story={story} reaction={story.reaction} onReact={onReact} />
        <p className="flex items-center gap-1 text-sm font-medium">
          Read story
          <ArrowRight
            aria-hidden="true"
            className="size-4 transition-transform group-hover:translate-x-0.5"
          />
        </p>
      </div>
    </article>
  );
}

function Edition({
  stories,
  paper,
  onOpen,
  onReact,
}: {
  stories: Story[];
  paper: Newspaper;
  onOpen: Props["onOpen"];
  onReact: React_;
}) {
  if (!stories.length)
    return (
      <EmptyState
        className="mt-6"
        title="Nothing material in this edition"
        description="No metric you can read moved beyond its threshold. The next edition is pressed on the view's schedule."
      />
    );
  const critical = stories.filter((story) => story.severity === "critical").length;
  const views = new Map(paper.sections.map((section) => [section.view_id, section.name]));
  return (
    <>
      <p className="flex flex-wrap gap-x-6 gap-y-1 border-b-2 border-foreground py-3 text-sm">
        <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Edition at a glance
        </span>
        <span className="tabular-nums">
          {stories.length} {stories.length === 1 ? "story" : "stories"}
        </span>
        <span className="tabular-nums">{critical} critical</span>
        <span className="tabular-nums">{stories.length - critical} material</span>
      </p>
      <div>
        {stories.map((story, index) => (
          <Entry
            key={story.id}
            story={story}
            position={index}
            onOpen={onOpen}
            onReact={onReact}
          />
        ))}
      </div>
      <p className="border-t-2 border-foreground pt-3 text-xs text-muted-foreground">
        End of edition ·{" "}
        {[...new Set(stories.map((story) => views.get(story.view_id)))]
          .filter((name): name is string => !!name)
          .map(humanize)
          .join(" · ")}
      </p>
    </>
  );
}

function shiftDay(value: string, days: number): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return date.toISOString().slice(0, 10);
}

export function NewspaperPage({
  story,
  edition,
  onEdition,
  onOpen,
  onAlerts,
  onFollowUp,
}: Props) {
  const epoch = useAuthStore((state) => state.securityEpoch);
  const client = useQueryClient();
  const key = ["newspaper", epoch, edition ?? "latest"];
  const paper = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => newspaperApi.read(edition, signal),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  // The order is the one the page loaded with. A reaction marks the story at
  // once and reshapes the order on the next load, so nothing jumps under the
  // reader's cursor.
  const react = useMutation({
    mutationFn: ({ id, reaction }: { id: string; reaction: Reaction | null }) =>
      newspaperApi.react(id, reaction),
    onMutate: ({ id, reaction }) => {
      const previous = client.getQueryData<Newspaper>(key);
      client.setQueryData<Newspaper>(key, (current) =>
        current
          ? {
              ...current,
              sections: current.sections.map((section) => ({
                ...section,
                stories: section.stories.map((item) =>
                  item.id === id ? { ...item, reaction } : item,
                ),
              })),
            }
          : current,
      );
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) client.setQueryData(key, context.previous);
    },
  });
  const onReact: React_ = (id, reaction) => react.mutate({ id, reaction });
  const stories = (paper.data?.sections ?? [])
    .flatMap((section) => section.stories)
    .sort(byPriority);
  const pressed = paper.data?.sections
    .map((section) => section.pressed_at)
    .sort()
    .pop();
  return (
    <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
        <header className="border-b-2 border-foreground pb-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h1 className="text-4xl font-semibold tracking-tight">News</h1>
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
          {onEdition && (edition || paper.data?.edition_date) ? (
            <div className="mt-3 flex flex-wrap items-center gap-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  onEdition(shiftDay(edition ?? paper.data!.edition_date!, -1))
                }
              >
                <ChevronLeft className="size-4" />
                Previous day
              </Button>
              {edition ? (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onEdition(shiftDay(edition, 1))}
                  >
                    Next day
                    <ChevronRight className="size-4" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => onEdition()}>
                    Latest edition
                  </Button>
                </>
              ) : null}
            </div>
          ) : null}
          <p className="mt-2 text-sm text-muted-foreground">
            {paper.data?.edition_date
              ? `${longDate(paper.data.edition_date)} · ${paper.data.sections.length} ${paper.data.sections.length === 1 ? "view" : "views"} covered${pressed ? ` · Pressed ${new Date(pressed).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" })}` : ""}`
              : edition
                ? `${longDate(edition)}${paper.isFetching ? "" : " · no edition was pressed for this day"}`
                : "Material changes in the business, from the Semantic Views you can read."}
          </p>
        </header>
        {react.isError && (
          <p role="alert" className="mt-4 text-sm text-destructive">
            Your reaction could not be saved. Try again.
          </p>
        )}
        {paper.isError ? (
          <div className="mt-6">
            <NewsFailure error={paper.error} retry={() => void paper.refetch()} />
          </div>
        ) : paper.isFetching && !paper.data ? (
          <LoadingLines className="mt-6" />
        ) : paper.data && paper.data.sections.length ? (
          <Edition stories={stories} paper={paper.data} onOpen={onOpen} onReact={onReact} />
        ) : (
          <EmptyState
            className="mt-6"
            title={edition ? "No edition for this day" : "No edition to read yet"}
            description={
              edition
                ? "Nothing was pressed for this day, or your role cannot read the views it covered."
                : "News appears after it is switched on for a Semantic View you can read and the first edition is pressed."
            }
          />
        )}
      </div>
      {story && (
        <StoryPanel
          key={`${epoch}:${story}`}
          id={story}
          ids={stories.map((item) => item.id)}
          reaction={stories.find((item) => item.id === story)?.reaction}
          epoch={epoch}
          onOpen={onOpen}
          onReact={onReact}
          onFollowUp={onFollowUp}
        />
      )}
    </div>
  );
}
