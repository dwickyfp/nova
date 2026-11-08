/**
 * The free stretch of Superset's own top bar, where Studio's workspace switch
 * and the Assistant button can dock instead of taking a header row of their
 * own (docs/arch-19-studio-dataviz.md).
 *
 * Superset is served same-origin under /bi, so its bar is measured directly:
 * the end of its brand and main menu (`[data-test="navbar-top"]`, Superset
 * 6.1.0) on the left, the start of its right-hand actions on the right. The
 * bar is one row only on wide frames; when Superset stacks it (narrow frames)
 * or the page has no such bar, there is no free stretch and Studio keeps its
 * own row, so nothing of Superset is ever covered.
 */
export type BarGap = {
  /** Distances from the frame's left edge, in CSS pixels. */
  left: number;
  right: number;
  /** The bar's height; the docked controls center on it. */
  height: number;
};

/** Superset's single-row bar is 60px tall; a stacked one is several rows. */
const MAX_ROW_HEIGHT = 72;

function width(el: Element): boolean {
  const box = el.getBoundingClientRect();
  return box.width > 0 && box.height > 0;
}

export function measureBarGap(frame: HTMLIFrameElement | null): BarGap | null {
  try {
    const doc = frame?.contentDocument;
    const header = doc?.querySelector("header");
    const menu = header?.querySelector('[data-test="navbar-top"]');
    if (!header || !menu) return null;
    const bar = header.getBoundingClientRect();
    if (bar.top > 1 || bar.height <= 0 || bar.height > MAX_ROW_HEIGHT) return null;
    const leftColumn = menu.closest(".ant-col") ?? menu.parentElement;
    const rightColumn = leftColumn?.nextElementSibling ?? null;
    // The menu list itself can span its whole column; its items and the
    // brand beside it are what take room.
    const leftItems = [
      ...Array.from(leftColumn?.children ?? []).filter((el) => el !== menu),
      ...Array.from(menu.children),
    ].filter(width);
    const left = Math.max(bar.left, ...leftItems.map((el) => el.getBoundingClientRect().right));
    const rightItems = Array.from(rightColumn?.querySelectorAll("li, button, a") ?? []).filter(width);
    const right = rightItems.length
      ? Math.min(...rightItems.map((el) => el.getBoundingClientRect().left))
      : bar.right;
    if (!(right > left)) return null;
    return { left, right, height: bar.height };
  } catch {
    return null;
  }
}

/** Superset's bar is there and stacked (a narrow frame): it leaves no room. */
export function supersetBarStacked(frame: HTMLIFrameElement | null): boolean {
  try {
    const header = frame?.contentDocument?.querySelector("header");
    return Boolean(header && header.getBoundingClientRect().height > MAX_ROW_HEIGHT);
  } catch {
    return false;
  }
}

/**
 * Where a control group `width` wide docks in `gap`, as a left offset from the
 * frame's edge: centered in the gap with `margin` clear on both sides, or null
 * when it does not fit.
 */
export function dockLeft(gap: BarGap | null, width: number, margin = 16): number | null {
  if (!gap || width <= 0) return null;
  const room = gap.right - gap.left - 2 * margin;
  if (room < width) return null;
  return Math.round(gap.left + (gap.right - gap.left - width) / 2);
}
