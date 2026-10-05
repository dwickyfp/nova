import { TONE_TEXT, tone } from "./format";
import type { Story } from "./newspaper-api";

/**
 * Narrative text with its verified figures and subjects emphasised.
 *
 * Only strings the server lists as proven values are styled, and they are
 * matched exactly, so nothing the writer invented can be dressed up as a fact.
 * The text is split into nodes; no markup from the narrative is interpreted.
 */
export function Highlighted({ text, story }: { text: string; story: Story }) {
  const marks = [...(story.highlights ?? [])]
    .filter((mark) => mark.text)
    .sort((a, b) => b.text.length - a.text.length);
  const nodes: React.ReactNode[] = [];
  let rest = text;
  let key = 0;
  while (rest) {
    let first: { at: number; mark: (typeof marks)[number] } | null = null;
    for (const mark of marks) {
      const at = rest.indexOf(mark.text);
      if (at >= 0 && (!first || at < first.at)) first = { at, mark };
    }
    if (!first) {
      nodes.push(rest);
      break;
    }
    if (first.at > 0) nodes.push(rest.slice(0, first.at));
    const { mark } = first;
    nodes.push(
      <span
        key={key++}
        data-highlight={mark.kind}
        className={
          mark.kind === "change"
            ? `font-semibold tabular-nums ${TONE_TEXT[tone(story)]}`
            : mark.kind === "figure"
              ? "font-semibold tabular-nums"
              : "font-semibold"
        }
      >
        {mark.text}
      </span>,
    );
    rest = rest.slice(first.at + mark.text.length);
  }
  return <>{nodes}</>;
}
