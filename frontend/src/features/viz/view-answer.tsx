import type { ViewInsight } from "./insight";

/** A view's answer in words: the question, then the headline value. */
export function ViewAnswer({ question, insight }: { question: string; insight: ViewInsight | null }) {
  return (
    <div className="max-w-[64ch]">
      {question ? <h2 className="type-question">{question}</h2> : null}
      {insight ? (
        <>
          <p className="type-answer mt-2 text-foreground">
            <span className={insight.lead.marked ? "text-viz-highlight" : undefined}>{insight.lead.subject}</span> {insight.lead.verb} {insight.lead.num}
            {insight.lead.unit ? <span className="type-unit">{insight.lead.unit}</span> : null}
          </p>
          {insight.detail ? <p className="type-body mt-2">{insight.detail}</p> : null}
        </>
      ) : null}
    </div>
  );
}
