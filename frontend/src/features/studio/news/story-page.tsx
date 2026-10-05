import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LoadingLines } from "@/components/ui/loading-overlay";
import { formatValue, humanize, kicker, longDate } from "./format";
import { newspaperApi, type StoryDetail } from "./newspaper-api";
import { Movement, NewsFailure, Severity } from "./newspaper-page";
import { DriverChart, TrendChart } from "./story-chart";

const RULE_SOURCE = {
  metric: "Metric definition",
  dimension: "Dimension",
  view: "Semantic View",
  threshold: "Reporting rule",
} as const;

function Body({
  story,
  onFollowUp,
}: {
  story: StoryDetail;
  onFollowUp: (prompt: string) => void;
}) {
  return (
    <article aria-labelledby="story-headline" className="min-w-0">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Severity story={story} />
        <span>{kicker(story)}</span>
      </div>
      <h1
        id="story-headline"
        className="mt-3 text-2xl font-semibold tracking-tight text-balance sm:text-3xl"
      >
        {story.narrative.headline}
      </h1>
      <p className="mt-2 text-base text-muted-foreground">{story.narrative.deck}</p>
      <p className="mt-3 border-y py-2 text-xs text-muted-foreground">
        {longDate(story.edition_date)} · {humanize(story.view_name)} ·{" "}
        {story.narrative_source === "model"
          ? "Written by the default model from verified figures"
          : "Standard wording from verified figures"}
      </p>
      <dl className="mt-5 grid gap-4 sm:grid-cols-3">
        <div>
          <dt className="text-xs text-muted-foreground">Change</dt>
          <dd><Movement story={story} large /></dd>
          <dd className="text-xs text-muted-foreground tabular-nums">
            {formatValue(story.change, story.unit)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Observed</dt>
          <dd className="text-2xl font-semibold break-words tabular-nums">
            {formatValue(story.after, story.unit)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Typical</dt>
          <dd className="text-2xl font-semibold break-words tabular-nums">
            {formatValue(story.before, story.unit)}
          </dd>
        </div>
      </dl>
      <div className="mt-6">
        <TrendChart story={story} />
      </div>
      <div className="mt-8 grid gap-8 lg:grid-cols-12">
        <div className="min-w-0 space-y-6 lg:col-span-7">
          <section aria-labelledby="what-happened">
            <h2 id="what-happened" className="text-sm font-semibold">What happened</h2>
            <p className="mt-1 text-sm">{story.narrative.what_happened}</p>
          </section>
          <section aria-labelledby="why-it-matters">
            <h2 id="why-it-matters" className="text-sm font-semibold">Why it matters</h2>
            <p className="mt-1 text-sm">{story.narrative.why_it_matters}</p>
          </section>
          <section aria-labelledby="what-to-check">
            <h2 id="what-to-check" className="text-sm font-semibold">What to check</h2>
            <p className="mt-1 text-sm">{story.narrative.what_to_check}</p>
          </section>
          {story.drivers.length ? (
            <section aria-labelledby="where-it-moved">
              <h2 id="where-it-moved" className="text-sm font-semibold">Where it moved</h2>
              <div className="mt-2">
                <DriverChart story={story} />
              </div>
            </section>
          ) : null}
          <Button
            onClick={() =>
              onFollowUp(
                `Look into this change from News: ${story.narrative.headline}. ${story.narrative.what_happened} What could explain it, and what should I check first?`,
              )
            }
          >
            Ask Studio about this
          </Button>
        </div>
        <aside aria-labelledby="business-rules" className="min-w-0 lg:col-span-5">
          <h2 id="business-rules" className="text-sm font-semibold">Business rules</h2>
          <ul className="mt-2 space-y-3">
            {story.business_rules.map((rule) => (
              <li key={`${rule.source}:${rule.name}`} className="rounded-md border bg-surface-2 p-3">
                <p className="text-xs text-muted-foreground">
                  {RULE_SOURCE[rule.source]} · {humanize(rule.name)}
                </p>
                <p className="mt-1 text-sm break-words">{rule.text}</p>
              </li>
            ))}
          </ul>
          <details className="mt-4 rounded-md border p-3 text-sm">
            <summary className="cursor-pointer font-medium focus-visible:outline focus-visible:outline-ring">
              How this story was verified
            </summary>
            <p className="mt-2 text-muted-foreground">
              Every figure comes from the published Semantic View, version{" "}
              {story.semantic_version}. Before this page opened, the same query ran
              with your active role and returned the same rows. If your access or the
              data changes, the story is withdrawn until the next edition.
            </p>
          </details>
        </aside>
      </div>
    </article>
  );
}

export function StoryPage({
  id,
  epoch,
  onBack,
  onFollowUp,
}: {
  id: string;
  epoch: number;
  onBack: () => void;
  onFollowUp: (prompt: string) => void;
}) {
  const story = useQuery({
    queryKey: ["newspaper", epoch, "story", id],
    queryFn: ({ signal }) => newspaperApi.story(id, signal),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  return (
    <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6">
        <Button variant="ghost" size="sm" className="mb-4 -ml-2" onClick={onBack}>
          <ArrowLeft className="size-4" />
          Back to News
        </Button>
        {story.isError ? (
          <NewsFailure error={story.error} retry={() => void story.refetch()} />
        ) : story.isFetching || !story.data ? (
          <LoadingLines />
        ) : (
          <Body story={story.data} onFollowUp={onFollowUp} />
        )}
      </div>
    </div>
  );
}
