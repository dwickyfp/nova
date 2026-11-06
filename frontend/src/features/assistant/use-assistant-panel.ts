import * as React from "react";

const NARROW_QUERY = "(max-width: 1023px)";

/**
 * The assistant panel is inline from `lg` (1024px) up and a `Sheet` overlay
 * below it. Inline below `lg`, the expanded sidebar plus the panel leave the
 * page only a sliver: at 800px the Workspaces editor was squeezed to about
 * 200px. The Sheet covers every width below `lg`, so no viewport falls into a
 * gap where the panel has no surface.
 */
export function useIsNarrowForAssistant() {
  return React.useSyncExternalStore(
    (callback) => {
      const mql = window.matchMedia(NARROW_QUERY);
      mql.addEventListener("change", callback);
      return () => mql.removeEventListener("change", callback);
    },
    () => window.matchMedia(NARROW_QUERY).matches,
    () => false,
  );
}
