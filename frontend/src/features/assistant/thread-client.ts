import { api } from "@/lib/api-client";

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
  /** The public trace of the turn: result tables, charts, citations, blocks. */
  steps?: Record<string, unknown>[];
  attachments?: { name: string; size_bytes: number; media_type: string }[];
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

/** The caller's own conversations whose title contains ``term``. */
export async function searchThreads(term: string): Promise<ThreadListResponse> {
  const params = new URLSearchParams({ q: term });
  return api.get<ThreadListResponse>(`/assistant/threads?${params}`);
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
