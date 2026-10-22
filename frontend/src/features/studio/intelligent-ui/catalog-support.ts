import { UI_CATALOG_VERSION } from "./catalog";
import { MAPCN_ENABLED } from "./map-flag";
import type { UiBlock } from "./types";

/** Whether this client renders the block's catalog rather than its fallback. */
export function rendersCatalog(block: UiBlock): boolean {
  return block.catalog_version === "nova-openui-2"
    || (MAPCN_ENABLED && block.catalog_version === UI_CATALOG_VERSION);
}
