import type { OrderedContent } from "./studio-chat";

/** Return the contiguous authored prefix that is safe to display. */
export function visibleContent(content: OrderedContent[]): OrderedContent[] {
  const ordered = [...content].sort((a, b) => a.index - b.index);
  const visible: OrderedContent[] = [];
  let expected = 0;
  for (const item of ordered) {
    if (item.index !== expected) break;
    visible.push(item);
    expected += 1;
    if (!item.complete) break;
  }
  return visible;
}
