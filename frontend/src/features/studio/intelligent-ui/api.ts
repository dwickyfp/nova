import { api } from "@/lib/api-client";
import type { UiSource, UiState } from "./types";

export function uiSourcePath(s: UiSource) {
  const thread = `threads/${encodeURIComponent(s.threadId)}/messages/${encodeURIComponent(s.messageId)}/ui-state/${encodeURIComponent(s.contentId)}`;
  // A Studio agent's answer, or a Neva answer in the assistant panel.
  return s.agentId
    ? `/agents/${encodeURIComponent(s.agentId)}/${thread}`
    : `/assistant/${thread}`;
}
export const uiApi = {
  mapMetric: (s: UiSource, event: "ready" | "failed" | "fallback" | "route_unavailable", elapsed_ms: number) =>
    api.post(`${uiSourcePath(s)}/map-metric`, { event, elapsed_ms }),
  get: (s: UiSource, signal?: AbortSignal) =>
    api.get<UiState>(uiSourcePath(s), signal),
  put: (s: UiSource, state: UiState) =>
    api.put<UiState>(uiSourcePath(s), state),
  media: (
    s: UiSource,
    type: string,
    props: Record<string, unknown>,
    signal: AbortSignal,
  ) =>
    api.post<Record<string, unknown>>(
      `${uiSourcePath(s)}/media`,
      { type, props },
      signal,
    ),
};
