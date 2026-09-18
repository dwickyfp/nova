import { useCallback, useEffect, useRef } from 'react'
import { createThread } from './thread-client'
import type { TurnContext } from './stream-client'
import { useAssistantTurn } from './use-assistant-turn'

export type WorkspaceAssistantOptions = {
  /** The file the assistant conversation is bound to; null when none is open. */
  fileId: string | null
  context: TurnContext
  onError?: (message: string) => void
}

/**
 * Binds the assistant to the workspace: one conversation per open file, created
 * on first send, with the active file's database/schema/role passed as turn
 * context. Switching files starts a fresh transcript, because a thread belongs
 * to one file and showing another file's transcript would misstate its context.
 *
 * This is the seam `WorkspacesPage` calls; keeping it here (rather than inline
 * in the page) is what makes the wiring testable.
 */
export function useWorkspaceAssistant({ fileId, context, onError }: WorkspaceAssistantOptions) {
  const threadsRef = useRef<Record<string, string>>({})
  const fileRef = useRef<string | null>(null)

  const ensureThread = useCallback(async () => {
    if (!fileId) return null
    const existing = threadsRef.current[fileId]
    if (existing) return existing
    const thread = await createThread(fileId)
    threadsRef.current[fileId] = thread.thread_id
    return thread.thread_id
  }, [fileId])

  const assistant = useAssistantTurn({ ensureThread, context, onError })
  const { startConversation } = assistant
  const startConversationRef = useRef(startConversation)
  startConversationRef.current = startConversation

  useEffect(() => {
    if (fileRef.current === fileId) return
    fileRef.current = fileId
    void startConversationRef.current()
  }, [fileId])

  return assistant
}
