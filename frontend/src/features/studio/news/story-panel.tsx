import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LoadingLines } from "@/components/ui/loading-overlay";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { formatValue, humanize, kicker, longDate } from "./format";
import { Highlighted } from "./highlight";
import { newspaperApi, type Reaction, type StoryDetail } from "./newspaper-api";
import { Reactions } from "./reactions";
import { DriverChart, TrendChart, WeekdayChart } from "./story-chart";
import { HeadFigure, NewsFailure, Severity } from "./story-parts";

const RULE_SOURCE = {
  metric: "Metric definition",
  dimension: "Dimension",
  view: "Semantic View",
  threshold: "Reporting rule",
} as const;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t py-5">
      <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {title}
      </h3>
      <div className="mt-2 text-sm leading-relaxed">{children}</div>
    </section>
  );
}

function Body({
  story,
  onFollowUp,
}: {
  story: StoryDetail;
  onFollowUp: (prompt: string) => void;
}) {
  return (
    <article className="min-w-0">
      <div className="flex flex-wrap items-center gap-2 text-xs tracking-wide text-muted-foreground uppercase">
        <Severity story={story} />
        <span>{kicker(story)}</span>
      </div>
      <SheetTitle className="mt-3 text-2xl font-semibold tracking-tight text-balance sm:text-3xl">
        {story.narrative.headline}
      </SheetTitle>
      <SheetDescription className="mt-2 text-base text-muted-foreground">
        {story.narrative.deck}
      </SheetDescription>
      <p className="mt-4 text-xs text-muted-foreground">
        {longDate(story.edition_date)} · {humanize(story.view_name)} ·{" "}
        {story.narrative_source === "model"
          ? "Written by the default model from verified figures"
          : "Standard wording from verified figures"}
      </p>
      <div className="mt-6 border-y py-5">
        <HeadFigure story={story} large />
        <p className="mt-2 text-xs text-muted-foreground tabular-nums">
          Observed {formatValue(story.after, story.unit)} · typical{" "}
          {formatValue(story.before, story.unit)}
        </p>
      </div>
      <div className="py-6">
        <TrendChart story={story} height={280} />
      </div>
      <Section title="What happened">
        <Highlighted text={story.narrative.what_happened} story={story} />
      </Section>
      <Section title="Why it matters">{story.narrative.why_it_matters}</Section>
      <Section title="What to check">{story.narrative.what_to_check}</Section>
      {story.drivers.length ? (
        <Section title="Where it moved">
          <DriverChart story={story} />
        </Section>
      ) : (
        <Section title="Against the same weekday">
          <WeekdayChart story={story} height={180} />
        </Section>
      )}
      <Section title="Business rules">
        <ul className="divide-y">
          {story.business_rules.map((rule) => (
            <li key={`${rule.source}:${rule.name}`} className="py-3 first:pt-0 last:pb-0">
              <p className="text-xs text-muted-foreground">
                {RULE_SOURCE[rule.source]} · {humanize(rule.name)}
              </p>
              <p className="mt-1 break-words">{rule.text}</p>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="How this story was verified">
        <p className="text-muted-foreground">
          Every figure comes from the published Semantic View, version{" "}
          {story.semantic_version}. Before this panel opened, the same query ran
          with your active role and returned the same rows. If your access or the
          data changes, the story is withdrawn until the next edition.
        </p>
      </Section>
      <div className="border-t pt-5">
        <Button
          onClick={() =>
            onFollowUp(
              `From News: ${story.narrative.headline}. What could explain this change, and what should I check first?`,
            )
          }
        >
          Ask Studio about this
        </Button>
      </div>
    </article>
  );
}

/** One story in a panel beside the edition, so the reader keeps their place. */
export function StoryPanel({
  id,
  ids,
  reaction,
  epoch,
  onOpen,
  onReact,
  onFollowUp,
}: {
  /** The reader's reaction as the edition page holds it. */
  reaction: Reaction | null | undefined;
  onReact: (id: string, reaction: Reaction | null) => void;
  id: string;
  /** Stories the reader can see, in page order, for previous and next. */
  ids: string[];
  epoch: number;
  onOpen: (id?: string) => void;
  onFollowUp: (prompt: string) => void;
}) {
  const story = useQuery({
    queryKey: ["newspaper", epoch, "story", id],
    queryFn: ({ signal }) => newspaperApi.story(id, signal),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  const position = ids.indexOf(id);
  const previous = position > 0 ? ids[position - 1] : undefined;
  const next = position >= 0 && position < ids.length - 1 ? ids[position + 1] : undefined;
  return (
    <Sheet open onOpenChange={(open) => !open && onOpen()}>
      <SheetContent
        side="right"
        aria-describedby={undefined}
        className="w-full gap-0 overflow-hidden p-0 sm:max-w-2xl"
      >
        <div className="flex shrink-0 items-center gap-1 border-b py-2 ps-4 pe-14">
          <Button
            variant="ghost"
            size="sm"
            disabled={!previous}
            onClick={() => onOpen(previous)}
          >
            <ChevronLeft className="size-4" />
            Previous
          </Button>
          <Button variant="ghost" size="sm" disabled={!next} onClick={() => onOpen(next)}>
            Next
            <ChevronRight className="size-4" />
          </Button>
          <span className="ms-auto flex items-center gap-2">
            {position >= 0 && (
              <span className="text-xs text-muted-foreground tabular-nums">
                {position + 1} of {ids.length}
              </span>
            )}
            {story.data && (
              <Reactions story={story.data} reaction={reaction} onReact={onReact} />
            )}
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6 sm:px-8">
          {story.isError ? (
            <>
              <SheetTitle className="sr-only">Story</SheetTitle>
              <NewsFailure error={story.error} retry={() => void story.refetch()} />
            </>
          ) : story.isFetching || !story.data ? (
            <>
              <SheetTitle className="sr-only">Loading story</SheetTitle>
              <LoadingLines />
            </>
          ) : (
            <Body story={story.data} onFollowUp={onFollowUp} />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
