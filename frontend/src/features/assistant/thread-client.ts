import { ApiError, api } from "@/lib/api-client";

// History is persisted in StarRocks; list and detail reads request bounded pages.

export type ThreadView = {
  thread_id: string;
  title: string;
  workspace_file_id: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
};

export type ThreadListResponse = {
  threads: ThreadView[];
  count: number;
  next_cursor?: string | null;
};

/** A stored message as returned by the thread detail endpoint. */
export type ThreadMessageView = {
  message_id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  created_at: string;
};

export type ThreadDetailResponse = {
  thread: ThreadView;
  messages: ThreadMessageView[];
  next_cursor?: string | null;
};

export async function createThread(
  workspaceFileId?: string | null,
): Promise<ThreadView> {
  return api.post<ThreadView>("/assistant/threads", {
    workspace_file_id: workspaceFileId ?? null,
  });
}

export async function listThreads(cursor?: string): Promise<ThreadListResponse> {
  return api.get<ThreadListResponse>(`/assistant/threads${historyQuery(cursor)}`);
}

export function historyQuery(cursor?: string): string {
  const params = new URLSearchParams({ limit: "50" });
  if (cursor) params.set("cursor", cursor);
  return `?${params}`;
}

export async function getThread(
  threadId: string,
  cursor?: string,
): Promise<ThreadDetailResponse> {
  return api.get<ThreadDetailResponse>(
    `/assistant/threads/${encodeURIComponent(threadId)}${historyQuery(cursor)}`,
  );
}

export async function renameThread(
  threadId: string,
  title: string,
): Promise<ThreadView> {
  return api.patch<ThreadView>(
    `/assistant/threads/${encodeURIComponent(threadId)}`,
    { title },
  );
}

export async function deleteThread(threadId: string): Promise<void> {
  await api.delete(`/assistant/threads/${encodeURIComponent(threadId)}`);
}

/**
 * Revokes the conversation's read-only always-allow grant (spec §6). The
 * backend takes no body and answers 204. A 404 means the thread is already
 * gone (the store is process-local, so a backend restart clears it) or is not
 * this user's; revoking a grant that no longer exists is a no-op, so 404
 * resolves rather than throwing. Network and other failures still reject.
 */
export async function resetGrant(threadId: string): Promise<void> {
  try {
    await api.delete(
      `/assistant/threads/${encodeURIComponent(threadId)}/grant`,
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return;
    throw error;
  }
}

/**
 * Sets or clears the conversation's read-only always-allow grant. The composer's
 * approval-mode selector calls this before a turn so a read-only query runs
 * without a per-call approval card. The backend answers `{ grant_active }`; a
 * 404 means the thread is gone (process-local store) or is not this user's, so
 * the requested mode cannot be persisted — that is reported as `false` rather
 * than thrown, matching `resetGrant`'s tolerance for a missing thread.
 */
export async function setGrant(
  threadId: string,
  alwaysAllow: boolean,
): Promise<boolean> {
  try {
    const response = await api.put<{ grant_active: boolean }>(
      `/assistant/threads/${encodeURIComponent(threadId)}/grant`,
      { always_allow_read_only: alwaysAllow },
    );
    return response.grant_active;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return false;
    throw error;
  }
}
