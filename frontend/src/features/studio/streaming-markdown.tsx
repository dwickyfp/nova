import { memo, useMemo, type ComponentProps } from "react";
import { Markdown } from "@/features/assistant/markdown";
import { markdownBlocks, useSmoothText } from "./streaming-text";

/**
 * Text that grows while it streams, shown without jumps.
 *
 * Two things keep a growing answer smooth:
 *
 * - **Reveal.** Text often arrives in bursts: a reasoning summary in one chunk,
 *   a verified answer in a few pieces at once. `useSmoothText` shows it at a
 *   reading pace that speeds up with the backlog, so a burst unrolls over a
 *   fraction of a second instead of appearing as a wall.
 * - **Stable blocks.** The text is split into Markdown blocks at blank lines.
 *   Finished blocks are memoised, so only the last one re-parses as it grows
 *   and nothing above it reflows.
 *
 * Only text this view watched arrive is revealed; a reopened conversation shows
 * its text at once. The final rendering is the same either way.
 */

type MarkdownProps = Omit<ComponentProps<typeof Markdown>, "children">;

const Block = memo(function Block({ text, ...props }: MarkdownProps & { text: string }) {
  return <Markdown {...props}>{text}</Markdown>;
});

export function StreamingMarkdown({
  text,
  animate,
  className,
  ...props
}: MarkdownProps & { text: string; animate: boolean }) {
  const shown = useSmoothText(text, animate);
  const blocks = useMemo(() => markdownBlocks(shown), [shown]);
  return (
    <div className={className} data-revealing={shown.length < text.length || undefined}>
      {blocks.map((block, index) => (
        <Block key={index} text={block} {...props} />
      ))}
    </div>
  );
}
