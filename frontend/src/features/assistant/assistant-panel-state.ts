/**
 * The persisted field is `assistant_collapsed` (true = hidden), while the panel
 * state is `assistantOpen` (true = visible). These two helpers are the only
 * place the polarity is translated, so a write and a read cannot drift apart.
 *
 * `assistant_collapsed` mirrors `sidebar_collapsed` and arrives on the
 * workspaces tree; the field is optional because it was absent before the Stage
 * B backend landed. Contract: roadmap T-D1 acceptance ("state survives reload
 * via workspace state") and
 * docs/specs/nova-61-agentic-assistant-design.md (E5a keeps conversation state
 * in memory; a layout boolean is not conversation state).
 */
export function initialAssistantOpen(tree: {
  assistant_collapsed?: boolean;
}): boolean {
  return !(tree.assistant_collapsed ?? true);
}

export function assistantCollapsedToPersist(open: boolean): boolean {
  return !open;
}

/**
 * Panel width bounds, in CSS pixels. MIN keeps the transcript readable (roughly
 * the original `w-[22rem]`); MAX stops the panel from consuming a viewport that
 * would squeeze the page content out of usefulness. The clamp is the single
 * source of truth for both the drag handle and a persisted value read back.
 */
export const ASSISTANT_MIN_WIDTH = 320;
export const ASSISTANT_MAX_WIDTH = 720;
export const ASSISTANT_DEFAULT_WIDTH = 352;

export function clampAssistantWidth(width: number): number {
  if (!Number.isFinite(width)) return ASSISTANT_DEFAULT_WIDTH;
  return Math.min(
    ASSISTANT_MAX_WIDTH,
    Math.max(ASSISTANT_MIN_WIDTH, Math.round(width)),
  );
}

/** Cookie name for the persisted width; mirrors the other UI prefs. */
export const ASSISTANT_WIDTH_COOKIE = "assistant_width";

export function parseAssistantWidth(value: string | undefined): number {
  if (!value) return ASSISTANT_DEFAULT_WIDTH;
  const parsed = Number.parseInt(value, 10);
  if (Number.isNaN(parsed)) return ASSISTANT_DEFAULT_WIDTH;
  return clampAssistantWidth(parsed);
}

/**
 * Vertical offset of the floating toggle, in CSS pixels, measured in screen
 * space: 0 keeps the button at its default `bottom-4` position, positive moves
 * it up and negative moves it down. The bounds are applied when a drag starts
 * (from the button's measured rect) but cannot be constants here because the
 * viewport is only known at runtime.
 */
export const ASSISTANT_DEFAULT_OFFSET_Y = 0;

/** Cookie name for the persisted toggle offset; mirrors the width. */
export const ASSISTANT_OFFSET_Y_COOKIE = "assistant_offset_y";

/**
 * Smallest gap kept between the toggle and the viewport edges, so a dragged
 * button never half-leaves the window or slides under the browser chrome.
 */
export const ASSISTANT_OFFSET_Y_MARGIN = 8;

/** Default `bottom` (in px) the toggle is anchored with, mirroring `bottom-4`. */
export const ASSISTANT_TOGGLE_REST_BOTTOM = 16;

/** Keyboard step for the drag handle's arrow-key path, mirroring the panel. */
export const ASSISTANT_OFFSET_Y_STEP = 16;

export function parseAssistantOffsetY(value: string | undefined): number {
  if (!value) return ASSISTANT_DEFAULT_OFFSET_Y;
  const parsed = Number.parseInt(value, 10);
  return Number.isNaN(parsed) ? ASSISTANT_DEFAULT_OFFSET_Y : parsed;
}

/**
 * Clamps a screen-space offset so the button's rect stays inside the viewport
 * with `margin` to spare. The rect is the *current* one (already offset by the
 * stored value), so the allowed offset shifts by that same amount: the button
 * cannot be pushed above the top or below the bottom edge.
 */
export function clampAssistantOffsetY(
  offset: number,
  rect: { top: number; bottom: number },
  viewportHeight: number,
  margin = ASSISTANT_OFFSET_Y_MARGIN,
): number {
  if (!Number.isFinite(offset)) return ASSISTANT_DEFAULT_OFFSET_Y;
  const maxUp = Math.max(0, rect.top - margin);
  const maxDown = Math.max(0, viewportHeight - margin - rect.bottom);
  // `+ 0` collapses a `-0` result to `0`, so a persisted value never reads back
  // as the negative zero that `String(-0)` would store.
  return Math.round(Math.min(maxUp, Math.max(-maxDown, offset))) + 0;
}
