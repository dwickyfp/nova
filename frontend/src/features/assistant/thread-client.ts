import { api } from '@/lib/api-client'

/**
 * Thread CRUD, mirroring `backend/app/modules/assistant/router.py`.
 * Threads and their messages live in process memory on the backend (E5a), so a
 * restart empties this list; the UI treats an empty list as "no conversations".
 */

export type ThreadView = {
  thread_id: string
  title: string
  workspace_file_id: string | null
  created_at: string
  updated_at: string
  message_count: number
}

export type ThreadListResponse = {
  threads: ThreadView[]
  count: number
}

export async function createThread(workspaceFileId?: string | null): Promise<ThreadView> {
  return api.post<ThreadView>('/assistant/threads', {
    workspace_file_id: workspaceFileId ?? null,
  })
}

export async function listThreads(): Promise<ThreadListResponse> {
  return api.get<ThreadListResponse>('/assistant/threads')
}

export async function renameThread(threadId: string, title: string): Promise<ThreadView> {
  return api.patch<ThreadView>(`/assistant/threads/${encodeURIComponent(threadId)}`, { title })
}

export async function deleteThread(threadId: string): Promise<void> {
  await api.delete(`/assistant/threads/${encodeURIComponent(threadId)}`)
}
