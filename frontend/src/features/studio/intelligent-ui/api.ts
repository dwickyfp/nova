import { api } from "@/lib/api-client";
import type { UiSource, UiState } from "./types";

export function uiSourcePath(s: UiSource) {
  return `/agents/${encodeURIComponent(s.agentId)}/threads/${encodeURIComponent(s.threadId)}/messages/${encodeURIComponent(s.messageId)}/ui-state/${encodeURIComponent(s.contentId)}`;
}
export const uiApi = {
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
