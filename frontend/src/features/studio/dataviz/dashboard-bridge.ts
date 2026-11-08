import { useSyncExternalStore } from "react";
import type { DatavizEditorState } from "./api";

/**
 * The Dataviz frame's state, read from the frame Nova created
 * (docs/arch-19-studio-dataviz.md, D4 and D5).
 *
 * Superset is served same-origin under ``/bi``, so Nova reads the framed
 * location directly instead of trusting messages: there is no postMessage
 * listener to forge. Three facts are taken from it:
 *
 * - the dashboard id, from the path;
 * - ``native_filters_key``, which the server resolves through Superset's
 *   filter-state API before any filter is called verified;
 * - unsaved changes, through Superset's own contract: a dashboard or chart
 *   with unsaved edits registers a ``beforeunload`` listener that calls
 *   ``preventDefault()``. Nova dispatches a synthetic cancelable
 *   ``beforeunload`` on the frame window and reads ``defaultPrevented``. No
 *   navigation happens and Superset's DOM is never inspected.
 */
export type DatavizFrameState = {
  path: string | null;
  dashboardId: number | null;
  nativeFiltersKey: string | null;
};

const EMPTY: DatavizFrameState = { path: null, dashboardId: null, nativeFiltersKey: null };
const DASHBOARD = /^\/bi\/superset\/dashboard\/(\d+)\/?/;
const FILTER_KEY = /^[A-Za-z0-9_-]{1,128}$/;

let frame: HTMLIFrameElement | null = null;
let state: DatavizFrameState = EMPTY;
const listeners = new Set<() => void>();

/** Parse a framed ``/bi`` path; anything outside Superset is no state. */
export function parseFramePath(path: string | null): DatavizFrameState {
  if (!path || !path.startsWith("/bi/")) return EMPTY;
  const url = new URL(path, "http://nova.invalid");
  const match = DASHBOARD.exec(url.pathname);
  const key = url.searchParams.get("native_filters_key");
  return {
    path,
    dashboardId: match ? Number(match[1]) : null,
    nativeFiltersKey: key && FILTER_KEY.test(key) ? key : null,
  };
}

function framedPath(node: HTMLIFrameElement | null): string | null {
  try {
    const location = node?.contentWindow?.location;
    if (!location) return null;
    const path = `${location.pathname}${location.search}`;
    return path.startsWith("/bi/") ? path : null;
  } catch {
    return null;
  }
}

function publish(next: DatavizFrameState) {
  if (
    next.path === state.path &&
    next.dashboardId === state.dashboardId &&
    next.nativeFiltersKey === state.nativeFiltersKey
  )
    return;
  state = next;
  listeners.forEach((listener) => listener());
}

/** Register the Dataviz frame; ``null`` when it unmounts. */
export function attachDatavizFrame(node: HTMLIFrameElement | null) {
  frame = node;
  publish(node ? parseFramePath(framedPath(node)) : EMPTY);
}

/** Re-read the frame's location (Superset routes client-side without loads). */
export function syncDatavizFrame(): DatavizFrameState {
  if (frame) publish(parseFramePath(framedPath(frame)));
  return state;
}

export function datavizFrameState(): DatavizFrameState {
  return state;
}

/** Whether the framed Superset page holds unsaved edits. */
export function probeUnsavedChanges(node: HTMLIFrameElement | null = frame): DatavizEditorState {
  try {
    const view = node?.contentWindow as (Window & typeof globalThis) | null | undefined;
    if (!view || !framedPath(node ?? null)) return "unknown";
    const event = new view.Event("beforeunload", { cancelable: true });
    view.dispatchEvent(event);
    return event.defaultPrevented ? "dirty" : "clean";
  } catch {
    return "unknown";
  }
}

/** Reload a clean frame so a manual edit starts from the latest layout. */
export function reloadDatavizFrameIfClean(): boolean {
  if (probeUnsavedChanges() !== "clean") return false;
  try {
    frame?.contentWindow?.location.reload();
    return true;
  } catch {
    return false;
  }
}

export function useDatavizFrameState(): DatavizFrameState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => state,
    () => EMPTY,
  );
}
