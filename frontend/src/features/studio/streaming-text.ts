import { useEffect, useRef, useState } from "react";

/**
 * Helpers for text that grows while it streams; see `StreamingMarkdown`.
 */

/**
 * Reveal speed, by elapsed time rather than frames so a slow or throttled
 * page catches up instead of falling behind. The backlog shrinks with a time
 * constant of `REVEAL_TAU_MS` (a 1,000-character burst is shown in about
 * half a second), and never slower than `MIN_CHARS_PER_SECOND`.
 */
const REVEAL_TAU_MS = 120;
const MIN_CHARS_PER_SECOND = 400;

/** Characters to reveal after `elapsed` ms with `backlog` still hidden. */
export function revealStep(backlog: number, elapsed: number): number {
  if (backlog <= 0) return 0;
  const dt = Math.max(0, elapsed);
  const share = Math.ceil(backlog * (1 - Math.exp(-dt / REVEAL_TAU_MS)));
  const floor = Math.ceil((MIN_CHARS_PER_SECOND * dt) / 1000);
  return Math.min(backlog, Math.max(1, share, floor));
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

/** The end of the word at or after `index`, so a reveal never stops mid-word. */
function wordEnd(text: string, index: number): number {
  if (index >= text.length) return text.length;
  const next = text.slice(index).search(/\s/);
  return next === -1 ? text.length : index + next;
}

/**
 * How much of `text` to show now. `animate` is read when the component mounts:
 * text that was already there (a replay) is shown whole.
 */
export function useSmoothText(text: string, animate: boolean): string {
  const [count, setCount] = useState(() => (animate && !prefersReducedMotion() ? 0 : text.length));
  const target = useRef(text);
  const previous = useRef(text);
  // The count the reveal loop works from; React may apply state later.
  const shown = useRef(count);

  // Text that was replaced rather than extended restarts from what still matches.
  if (previous.current !== text) {
    const before = previous.current;
    previous.current = text;
    if (!text.startsWith(before.slice(0, Math.min(count, before.length)))) {
      let same = 0;
      while (same < Math.min(count, text.length) && before[same] === text[same]) same += 1;
      if (same !== count) {
        shown.current = same;
        setCount(same);
      }
    }
  }
  target.current = text;

  const behind = count < text.length;
  useEffect(() => {
    if (!behind) return;
    if (prefersReducedMotion()) {
      shown.current = target.current.length;
      setCount(target.current.length);
      return;
    }
    let frame = 0;
    let last = performance.now();
    const step = (now: number) => {
      const elapsed = now - last;
      last = now;
      const goal = target.current.length;
      const current = shown.current;
      if (current >= goal) return;
      const next = current + revealStep(goal - current, elapsed);
      shown.current = next;
      setCount(next);
      if (next < goal) frame = window.requestAnimationFrame(step);
    };
    frame = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(frame);
  }, [behind]);

  return count >= text.length ? text : text.slice(0, wordEnd(text, count));
}

/**
 * Split Markdown at blank lines into blocks that render independently.
 *
 * A fenced code block stays whole, and an indented line after a blank line
 * (a list item's continuation) stays with the block before it.
 */
export function markdownBlocks(text: string): string[] {
  const blocks: string[] = [];
  let current: string[] = [];
  let fenced = false;
  let blank = false;
  for (const line of text.split("\n")) {
    if (!fenced && line.trim() === "") {
      blank = current.length > 0;
      continue;
    }
    if (blank && !/^\s/.test(line)) {
      blocks.push(current.join("\n"));
      current = [];
    } else if (blank) {
      current.push("");
    }
    blank = false;
    current.push(line);
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
  }
  if (current.length) blocks.push(current.join("\n"));
  return blocks;
}
